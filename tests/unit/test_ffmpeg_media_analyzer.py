"""The FFmpeg ``MediaAnalyzer`` reads FFmpeg's reports exactly, and never reports "nothing found"
for a report it could not read."""

from fractions import Fraction
from pathlib import Path

import pytest

from media_house.core.application.ports import ProcessResult, ProcessSpec, TimeSpan
from media_house.core.infrastructure.ffmpeg_media_analyzer import FfmpegMediaAnalyzer
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError

_STREAMS = (
    "Input #0, mov,mp4, from 'x.mp4':\n"
    "  Stream #0:0: Video: h264, yuv420p, 320x180, 25 fps\n"
    "  Stream #0:1: Audio: aac (LC), 48000 Hz, stereo, fltp\n"
)
_LOUDNESS = (
    "[Parsed_ebur128_0 @ 0x1] Summary:\n"
    "  Integrated loudness:\n    I:         -23.4 LUFS\n"
    "  Loudness range:\n    LRA:         4.2 LU\n"
    "  True peak:\n    Peak:       -1.5 dBFS\n"
    "Overall\n  Number of samples: 96000\n  Peak level dB: -3.1\n" + _STREAMS
)


class ScriptedRunner:
    """Answers by what the command line asks for."""

    def __init__(self, answers: dict[str, ProcessResult]) -> None:
        self._answers = answers
        self.calls: list[list[str]] = []

    def run(
        self, spec: ProcessSpec, *, cancellation: object = None, on_output: object = None
    ) -> ProcessResult:
        args = list(spec.args)
        self.calls.append(args)
        joined = " ".join(args)
        for needle, answer in reversed(self._answers.items()):  # the specific one wins
            if needle in joined:
                return answer
        return ProcessResult(1, "", "", 0.0)


def _ok(stderr: str, stdout: str = "") -> ProcessResult:
    return ProcessResult(0, stdout, stderr, 0.0)


def _analyzer(answers: dict[str, ProcessResult]) -> FfmpegMediaAnalyzer:
    answers = {"-i x.mp4": ProcessResult(1, "", _STREAMS, 0.0), **answers}
    return FfmpegMediaAnalyzer(ScriptedRunner(answers))


X = Path("x.mp4")
TOKEN = CancellationToken()


def test_black_spans_are_exact_fractions() -> None:
    log = "[blackdetect @ 0x1] black_start:2 black_end:3.04 black_duration:1.04\n"
    analyzer = _analyzer({"blackdetect": _ok(log)})

    spans = analyzer.black_spans(
        X, min_seconds=0.1, picture_threshold=0.98, pixel_threshold=0.1, cancellation=TOKEN
    )

    assert spans == (TimeSpan(Fraction(2), Fraction(304, 100)),)


def test_a_freeze_runs_from_its_start_for_its_duration() -> None:
    log = (
        "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 1.5\n"
        "[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 2\n"
        "[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 3.5\n"
        "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 8\n"
    )
    analyzer = _analyzer({"freezedetect": _ok(log)})

    spans = analyzer.frozen_spans(X, min_seconds=1.0, noise_db=-60.0, cancellation=TOKEN)

    assert spans == (TimeSpan(Fraction(3, 2), Fraction(7, 2)),)


def test_silence_that_lasts_to_the_end_has_no_end() -> None:
    log = (
        "[silencedetect @ 0x1] silence_start: 0.5\n"
        "[silencedetect @ 0x1] silence_end: 1.25 | silence_duration: 0.75\n"
        "[silencedetect @ 0x1] silence_start: 9\n"
    )
    analyzer = _analyzer({"silencedetect": _ok(log)})

    spans = analyzer.silent_spans(X, min_seconds=0.5, noise_db=-50.0, cancellation=TOKEN)

    assert spans == (TimeSpan(Fraction(1, 2), Fraction(5, 4)), TimeSpan(Fraction(9), None))
    assert spans[1].length is None


def test_loudness_is_read_from_the_summary() -> None:
    analyzer = _analyzer({"ebur128": _ok(_LOUDNESS)})

    reading = analyzer.loudness(X, span=None, cancellation=TOKEN)

    assert reading.integrated_lufs == pytest.approx(-23.4)
    assert reading.loudness_range_lu == pytest.approx(4.2)
    assert reading.true_peak_dbtp == pytest.approx(-1.5)
    assert reading.sample_peak_dbfs == pytest.approx(-3.1)
    assert (reading.sample_rate, reading.channels, reading.duration_samples) == (48000, 2, 96000)


def test_a_range_is_measured_with_an_input_seek() -> None:
    runner = ScriptedRunner(
        {"-i x.mp4": ProcessResult(1, "", _STREAMS, 0.0), "-i x.mp4 -vn": _ok(_LOUDNESS)}
    )

    FfmpegMediaAnalyzer(runner).loudness(
        X, span=TimeSpan(Fraction(5, 2), Fraction(5)), cancellation=TOKEN
    )

    measuring = next(
        c
        for c in runner.calls
        if "ebur128=peak=true:framelog=quiet,astats=measure_perchannel=none" in c
    )
    assert measuring[measuring.index("-ss") + 1] == "2.500000"
    assert measuring[measuring.index("-t") + 1] == "2.500000"


def test_a_file_without_audio_is_said_to_have_none() -> None:
    silent_video = ProcessResult(1, "", "  Stream #0:0: Video: h264, yuv420p, 320x180\n", 0.0)
    analyzer = FfmpegMediaAnalyzer(ScriptedRunner({"-i x.mp4": silent_video}))

    reading = analyzer.loudness(X, span=None, cancellation=TOKEN)

    assert reading.has_audio is False
    assert reading.integrated_lufs is None
    assert analyzer.silent_spans(X, min_seconds=0.5, noise_db=-50.0, cancellation=TOKEN) == ()


def test_an_unreadable_loudness_report_is_an_error_not_silence() -> None:
    analyzer = _analyzer({"ebur128": _ok("nothing useful here\n" + _STREAMS)})

    with pytest.raises(ExternalSystemError):
        analyzer.loudness(X, span=None, cancellation=TOKEN)


def test_a_clean_decode_is_complete_and_a_noisy_one_is_not() -> None:
    clean = _analyzer({"-progress": _ok("", "frame=125\nout_time_us=5000000\nprogress=end\n")})
    noisy = _analyzer(
        {"-progress": _ok("[h264 @ 0x1] error while decoding MB 3 4\n", "frame=40\nprogress=end\n")}
    )

    good = clean.decode(X, cancellation=TOKEN)
    bad = noisy.decode(X, cancellation=TOKEN)

    assert good.complete and good.frames_decoded == 125
    assert good.decoded_until == Fraction(5)
    assert not bad.complete and "error while decoding" in bad.errors[0]


def test_a_failing_decode_is_incomplete_even_without_messages() -> None:
    analyzer = _analyzer({"-progress": ProcessResult(69, "", "", 0.0)})

    report = analyzer.decode(X, cancellation=TOKEN)

    assert not report.complete
    assert report.errors == ("exit code 69",)
