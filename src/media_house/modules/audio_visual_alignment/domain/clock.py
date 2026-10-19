"""The common clock, and a coarse check that the two streams agree on it.

Convention (one sign, everywhere): ``common = upstream_seconds + correction``. The common clock is
the container timeline: zero is the container's ``start_time``. Audio Intelligence already
reports seconds on it (it adds ``audio_offset = audio_start - container_start``). Video
Intelligence reports the decoder's presentation timestamps, and FFmpeg does not always shift
those by the container start (measured: an MP4 whose container starts at 0.4768 s reports its
first frame at 0.5232 s, which is container-relative; the same material in MPEG-TS reports the
first frame at 0.0 s although it is 0.5232 s after the container start). So the video correction is
DERIVED from the inspection:

    expected = first presented video pts - container start      (inspection, same origin as audio)
    correction = expected - first frame seconds as analysed      (0 when the two already agree)

A correction comes from metadata or from the caller, never from the lag probe. The probe only
reports. Upstream seconds are kept on every record next to the common ones.
"""

import math
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from media_house.core.domain import TimeRange
from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.errors import InvalidAlignmentConfig
from media_house.modules.audio_visual_alignment.domain.inputs import (
    ClockFacts,
    MouthSample,
    SpeechActivity,
    VideoFacts,
)
from media_house.modules.audio_visual_alignment.domain.model import (
    ClockCorrection,
    LagProbe,
)
from media_house.modules.audio_visual_alignment.domain.scoring import pearson, spread
from media_house.modules.audio_visual_alignment.domain.values import (
    CorrectionSource,
    ProbeState,
)

_MIN_SAMPLES_PER_WINDOW = 3


@dataclass(frozen=True, slots=True)
class Clock:
    """Moves upstream seconds onto the common clock."""

    video_shift: float = 0.0
    audio_shift: float = 0.0

    def video(self, seconds: float) -> float:
        return seconds + self.video_shift

    def audio(self, seconds: float) -> float:
        return seconds + self.audio_shift

    def video_interval(self, span: TimeRange) -> tuple[float, float]:
        return self.video(span.start.seconds), self.video(span.end.seconds)

    @classmethod
    def of(cls, corrections: Sequence[ClockCorrection]) -> "Clock":
        by_stream = {c.stream: c.seconds for c in corrections}
        return cls(by_stream.get("video", 0.0), by_stream.get("audio", 0.0))


def build_corrections(
    clock: ClockFacts | None,
    video: VideoFacts,
    audio_shift: float | None,
    profile: AlignmentProfile,
) -> tuple[ClockCorrection, ...]:
    return (_video_correction(clock, video, profile), _audio_correction(audio_shift))


def _video_correction(
    clock: ClockFacts | None, video: VideoFacts, profile: AlignmentProfile
) -> ClockCorrection:
    observed = video.first_frame_seconds
    if (
        clock is None
        or clock.container_start is None
        or clock.video_first_pts is None
        or observed is None
    ):
        return ClockCorrection(
            stream="video",
            seconds=0.0,
            source=CorrectionSource.NONE,
            observed_seconds=observed,
            note="no stored inspection to check the video's time origin against",
        )
    expected = clock.video_first_pts - clock.container_start
    shift = expected - observed
    if abs(shift) <= profile.clock_tolerance_seconds:
        return ClockCorrection(
            stream="video",
            seconds=0.0,
            source=CorrectionSource.INSPECTION,
            observed_seconds=observed,
            expected_seconds=expected,
            note="the video analysis already uses the container timeline",
        )
    return ClockCorrection(
        stream="video",
        seconds=shift,
        source=CorrectionSource.INSPECTION,
        observed_seconds=observed,
        expected_seconds=expected,
        note="the video analysis does not start where the inspection says its first frame is",
    )


def _audio_correction(shift: float | None) -> ClockCorrection:
    if shift is None:
        return ClockCorrection(
            stream="audio",
            seconds=0.0,
            source=CorrectionSource.NONE,
            note="audio seconds are taken as reported (container timeline)",
        )
    return ClockCorrection(
        stream="audio",
        seconds=shift,
        source=CorrectionSource.SUPPLIED,
        note="the caller supplied the shift of this audio onto the video's clock",
    )


# --- the lag probe --------------------------------------------------------------------------------
def probe_lag(
    activity: SpeechActivity | None,
    mouth: Sequence[MouthSample],
    clock: Clock,
    profile: AlignmentProfile,
) -> LagProbe:
    """Correlate speech activity with the spread of the jaw opening over a grid of lags.

    A positive lag means the mouth moves LATER than the speech on the common clock.
    """
    hop = profile.probe_hop_seconds
    steps = profile.probe_lag_step_seconds / hop
    if abs(steps - round(steps)) > 1e-9:
        raise InvalidAlignmentConfig("probe_lag_step_seconds must be a multiple of the hop")
    resolution = profile.probe_window_seconds
    if activity is None or len(mouth) < _MIN_SAMPLES_PER_WINDOW:
        return LagProbe(
            state=ProbeState.INSUFFICIENT,
            resolution_seconds=resolution,
            reason="speech activity or mouth measurements are missing",
        )
    times = sorted(clock.video(m.seconds) for m in mouth)
    jaws = [m.jaw for m in sorted(mouth, key=lambda m: m.seconds)]
    window = profile.probe_window_seconds
    first, last = math.floor(times[0] / hop), math.floor(times[-1] / hop)

    spreads: dict[int, float] = {}
    speech: dict[int, float] = {}
    for j in range(first, last + 1):
        start = j * hop
        lo, hi = bisect_left(times, start), bisect_right(times, start + window)
        if hi - lo >= _MIN_SAMPLES_PER_WINDOW and (s := spread(jaws[lo:hi])) is not None:
            spreads[j] = s
    # speech activity is on the audio clock: common = audio + shift, so audio = common - shift
    margin = math.ceil(profile.probe_max_lag_seconds / hop)
    for j in range(first - margin, last + margin + 1):
        audio_start = j * hop - clock.audio_shift
        if (fraction := activity.fraction(audio_start, audio_start + window)) is not None:
            speech[j] = fraction

    max_steps = round(profile.probe_max_lag_seconds / profile.probe_lag_step_seconds)
    step_in_windows = round(steps)
    correlations: dict[float, tuple[float, int]] = {}
    for k in range(-max_steps, max_steps + 1):
        shift = k * step_in_windows
        pairs = [(speech[j], spreads[j + shift]) for j in speech if (j + shift) in spreads]
        if len(pairs) * hop < profile.probe_min_support_seconds:
            continue
        r = pearson([p[0] for p in pairs], [p[1] for p in pairs])
        if r is not None:
            correlations[k * profile.probe_lag_step_seconds] = (r, len(pairs))
    if not correlations:
        return LagProbe(
            state=ProbeState.INSUFFICIENT,
            resolution_seconds=resolution,
            reason="too little speech seen together with a measured mouth",
        )
    best_lag, (best_r, pairs_at_best) = max(correlations.items(), key=lambda item: item[1][0])
    support = pairs_at_best * hop
    if best_r < profile.probe_min_correlation:
        return LagProbe(
            state=ProbeState.INSUFFICIENT,
            correlation=best_r,
            windows=pairs_at_best,
            support_seconds=support,
            resolution_seconds=resolution,
            reason="speech and mouth movement do not clearly follow each other at any lag",
        )
    rivals = [r for lag, (r, _) in correlations.items() if abs(lag - best_lag) >= window]
    runner_up = max(rivals, default=None)
    state = ProbeState.CONSISTENT
    if runner_up is not None and runner_up >= profile.probe_ambiguity_ratio * best_r:
        state = ProbeState.AMBIGUOUS
    elif abs(best_lag) >= profile.probe_offset_seconds:
        state = ProbeState.OFFSET_INDICATED
    return LagProbe(
        state=state,
        lag_seconds=best_lag,
        correlation=best_r,
        runner_up_correlation=runner_up,
        windows=pairs_at_best,
        support_seconds=support,
        resolution_seconds=resolution,
    )
