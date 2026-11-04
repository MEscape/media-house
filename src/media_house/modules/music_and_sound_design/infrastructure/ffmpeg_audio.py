"""FFmpeg through the ``ProcessRunner`` port: decode to float PCM, measure EBU R128 loudness.

The runner captures text only, so PCM is decoded into a temporary raw ``f32le`` file and read
with numpy. A missing FFmpeg surfaces as ``ToolNotFoundError`` from the runner.
"""

import re
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.modules.music_and_sound_design.domain.analysis import Loudness
from media_house.modules.music_and_sound_design.domain.errors import UnreadableSound
from media_house.shared.concurrency import CancellationToken

_TIMEOUT_SECONDS = 900.0
_VERSION_TIMEOUT_SECONDS = 60.0
_VERSION = re.compile(r"version\s+(\S+)")
_SUMMARY_NUMBER = r"\s+(-?(?:inf|\d+(?:\.\d+)?))"
_INTEGRATED = re.compile(r"I:" + _SUMMARY_NUMBER + r" LUFS")
_RANGE = re.compile(r"LRA:" + _SUMMARY_NUMBER + r" LU\b")
_PEAK = re.compile(r"Peak:" + _SUMMARY_NUMBER + r" dBFS")
#: ebur128 reports this for programmes that never pass its gate (very short or silent).
_GATE_FLOOR_LUFS = -69.9


class FfmpegAudio:
    def __init__(self, runner: ProcessRunner, *, ffmpeg: str = "ffmpeg") -> None:
        self._runner = runner
        self._ffmpeg = ffmpeg
        self._version: str | None = None

    @property
    def executable(self) -> str:
        return self._ffmpeg

    @property
    def version(self) -> str:
        """FFmpeg's own version: its decoders and ebur128 are part of a measurement's identity."""
        if self._version is None:
            result = self._runner.run(
                ProcessSpec(
                    self._ffmpeg, ["-version"], timeout_seconds=_VERSION_TIMEOUT_SECONDS, check=True
                )
            )
            first = result.stdout.splitlines()[0] if result.stdout else ""
            match = _VERSION.search(first)
            self._version = match.group(1) if match else "unknown"
        return self._version

    def run(self, args: list[str], cancellation: CancellationToken) -> str:
        """Run FFmpeg; returns its log (stderr). A failure raises ``UnreadableSound``."""
        result = self._runner.run(
            ProcessSpec(
                self._ffmpeg,
                ["-hide_banner", "-nostdin", "-y", *args],
                timeout_seconds=_TIMEOUT_SECONDS,
            ),
            cancellation=cancellation,
        )
        if not result.succeeded:
            lines = result.stderr.strip().splitlines() or ["unknown FFmpeg error"]
            raise UnreadableSound(lines[-1])
        return result.stderr

    def decode_mono(
        self,
        source: Path,
        destination: Path,
        sample_rate: int,
        cancellation: CancellationToken,
    ) -> NDArray[np.float64]:
        """The first audio stream, down-mixed to mono at ``sample_rate``, as float64 samples."""
        self.run(
            [
                "-loglevel", "error", "-i", str(source), "-map", "0:a:0", "-ac", "1",
                "-ar", str(sample_rate), "-f", "f32le", "-c:a", "pcm_f32le", str(destination),
            ],
            cancellation,
        )  # fmt: skip
        samples = np.fromfile(destination, dtype="<f4").astype(np.float64)
        if not len(samples):
            raise UnreadableSound("the file decodes to no audio")
        return samples

    def loudness(self, source: Path, cancellation: CancellationToken) -> Loudness:
        """Integrated loudness, loudness range and true peak (FFmpeg ``ebur128``)."""
        log = self.run(
            [
                "-loglevel", "info", "-i", str(source), "-map", "0:a:0",
                "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-",
            ],
            cancellation,
        )  # fmt: skip
        summary = log[log.rfind("Summary:") :] if "Summary:" in log else ""
        return parse_loudness(summary)


def parse_loudness(summary: str) -> Loudness:
    def number(pattern: re.Pattern[str]) -> float | None:
        found = pattern.search(summary)
        if found is None or "inf" in found.group(1):
            return None
        return float(found.group(1))

    integrated = number(_INTEGRATED)
    gated = integrated is None or integrated <= _GATE_FLOOR_LUFS
    return Loudness(
        integrated_lufs=None if gated else integrated,
        loudness_range_lu=None if gated else number(_RANGE),
        true_peak_dbtp=number(_PEAK),
    )
