"""Judge the finished mix against its specification and delivery profile.

Every finding says how sure it is (confirmed technical failure, likely problem, recommendation,
intentional) and where it is. Silence where nothing was planned, a quiet passage and an unusual
dynamic are never errors by themselves.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from media_house.modules.audio_mix_engine.domain.result import Measurement, OutputIssue
from media_house.modules.audio_mix_engine.domain.spec import AudioMixSpecification
from media_house.modules.audio_mix_engine.domain.values import Certainty, Severity

C, L, R, N = Certainty.CONFIRMED, Certainty.LIKELY, Certainty.RECOMMENDATION, Certainty.INTENTIONAL


@dataclass(frozen=True, slots=True)
class OutputFacts:
    mix: Measurement
    stems: Mapping[str, Measurement]
    #: Silent stretches of the mix, in seconds.
    silences: Sequence[tuple[float, float]]
    #: Loudness of the dialogue stem and of the music stem, measured over the speech ranges.
    dialogue_lufs: float | None
    music_lufs: float | None
    #: Peak of (stems summed through the same master) minus (the mix), in dB; ``None``: not checked.
    recombination_db: float | None


def validate_output(spec: AudioMixSpecification, facts: OutputFacts) -> tuple[OutputIssue, ...]:
    found: list[OutputIssue] = []
    target = spec.loudness
    m = facts.mix
    rate = spec.render.sample_rate

    def add(code: str, sev: Severity, cert: Certainty, msg: str, **kw: object) -> None:
        found.append(OutputIssue(code, sev, cert, msg, **kw))  # type: ignore[arg-type]

    if m.integrated_lufs is None:
        add("silent_mix", Severity.CRITICAL, C, "the mix is silent or too quiet to measure")
    else:
        off = m.integrated_lufs - target.integrated_lufs
        if abs(off) > target.tolerance_lu:
            add(
                "loudness_out_of_range", Severity.ERROR, C,
                f"integrated loudness {m.integrated_lufs:.1f} LUFS, "
                f"target {target.integrated_lufs:.1f} "
                f"(±{target.tolerance_lu:g} LU)",
            )  # fmt: skip
    if m.true_peak_dbtp is not None and m.true_peak_dbtp > target.true_peak_dbtp + 0.1:
        add(
            "true_peak_over", Severity.ERROR, C,
            f"true peak {m.true_peak_dbtp:.1f} dBTP is over the "
            f"{target.true_peak_dbtp:.1f} ceiling",
        )  # fmt: skip
    if m.sample_peak_dbfs is not None and m.sample_peak_dbfs >= -0.1:
        sev = Severity.CRITICAL if m.sample_peak_dbfs > 0.0 else Severity.ERROR
        add("clipping", sev, C, f"sample peak {m.sample_peak_dbfs:.2f} dBFS: the mix clips")
    if (
        target.max_loudness_range_lu is not None
        and m.loudness_range_lu is not None
        and m.loudness_range_lu > target.max_loudness_range_lu
    ):
        add(
            "loudness_range", Severity.WARNING, L,
            f"loudness range {m.loudness_range_lu:.1f} LU is wider than "
            f"{target.max_loudness_range_lu:g}",
        )  # fmt: skip
    if m.sample_rate != rate or m.channels != spec.render.channels or m.codec != spec.render.codec:
        add(
            "format", Severity.ERROR, C,
            f"the file is {m.codec} {m.sample_rate} Hz {m.channels} ch, "
            f"the delivery wants {spec.render.codec} {rate} Hz {spec.render.channels} ch",
        )  # fmt: skip
    wanted = round(spec.timebase.duration_samples * rate / spec.timebase.sample_rate)
    tolerance = max(2, round(rate / float(spec.timebase.frame_rate.value)))
    if abs(m.duration_samples - wanted) > tolerance:
        add(
            "duration_mismatch", Severity.ERROR, C,
            f"the mix is {m.duration_samples} samples, the timeline {wanted}",
        )  # fmt: skip
    for stem_id, s in sorted(facts.stems.items()):
        if abs(s.duration_samples - m.duration_samples) > 1 or s.sample_rate != m.sample_rate:
            add(
                "stem_not_aligned", Severity.ERROR, C,
                f"{stem_id} is {s.duration_samples} samples at {s.sample_rate} Hz, the mix "
                f"{m.duration_samples} at {m.sample_rate} Hz", element_id=stem_id,
            )  # fmt: skip
        if s.channels != m.channels:
            add(
                "stem_channels",
                Severity.ERROR,
                C,
                f"{stem_id} has {s.channels} channels",
                element_id=stem_id,
            )
    if facts.dialogue_lufs is not None and facts.music_lufs is not None:
        gap = facts.dialogue_lufs - facts.music_lufs
        if gap < spec.validation.min_dialogue_over_music_lu:
            add(
                "music_masks_dialogue", Severity.WARNING, L,
                f"under speech the music is only {gap:.1f} LU below the dialogue "
                f"(the profile wants {spec.validation.min_dialogue_over_music_lu:g})",
                evidence=(
                    f"dialogue {facts.dialogue_lufs:.1f} LUFS, music {facts.music_lufs:.1f} LUFS"
                ),
            )  # fmt: skip
        most = spec.validation.max_dialogue_over_music_lu
        if most is not None and gap > most:
            add(
                "music_inaudible", Severity.WARNING, L,
                f"under speech the music is {gap:.1f} LU below the dialogue: too far to be heard "
                f"(at most {most:g})",
                evidence=(
                    f"dialogue {facts.dialogue_lufs:.1f} LUFS, music {facts.music_lufs:.1f} LUFS"
                ),
            )  # fmt: skip
    for start, end in facts.silences:
        a, b = round(start * rate), round(end * rate)
        scale = rate / spec.timebase.sample_rate
        covered = sum(
            max(0, min(b, round(c.end_sample * scale)) - max(a, round(c.start_sample * scale)))
            for c in spec.clips
        )
        if covered >= (b - a) / 2:
            add(
                "silence_over_planned_sound", Severity.WARNING, L,
                f"silence from {start:.2f}s to {end:.2f}s where sound was planned",
                start_sample=a, end_sample=b,
            )  # fmt: skip
        else:
            add(
                "silence_where_nothing_planned", Severity.INFO, N,
                f"silence from {start:.2f}s to {end:.2f}s: no sound is planned there",
                start_sample=a, end_sample=b,
            )  # fmt: skip
    if facts.recombination_db is None:
        add(
            "recombination_not_checked", Severity.INFO, R, "stems were not recombined to check them"
        )
    elif facts.recombination_db > spec.validation.recombination_tolerance_db:
        add(
            "recombination", Severity.ERROR, C,
            f"the stems recombined through the master differ from the mix by "
            f"{facts.recombination_db:.1f} dB",
        )  # fmt: skip
    return tuple(found)
