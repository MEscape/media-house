"""Quality analysis: EBU R128 loudness through FFmpeg plus the signal measurements of NumPy.

Structurally implements ``application.ports.QualityAnalyzer``. The audio is measured, never
modified; each call reads the file once for the signal metrics and runs one FFmpeg pass for
loudness (integrated LUFS, loudness range, true peak).
"""

import re
from pathlib import Path

from media_house.modules.audio_improvement.application.ports import EngineIdentity
from media_house.modules.audio_improvement.domain.measurements import QualityMeasurements
from media_house.modules.audio_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.modules.audio_improvement.infrastructure.ffmpeg_transcoder import read_wav
from media_house.modules.audio_improvement.infrastructure.quality_metrics import measure_signal
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError

#: Revision of the measurement algorithms; bump when ``quality_metrics.py`` changes results.
_REVISION = "1"
#: EBU R128 reports this for audio below the absolute gate (silence): "not measurable".
_GATED_LUFS = -70.0
_INTEGRATED = re.compile(r"^\s*I:\s+(-?\d+(?:\.\d+)?|-inf)\s+LUFS", re.MULTILINE)
_RANGE = re.compile(r"^\s*LRA:\s+(-?\d+(?:\.\d+)?)\s+LU", re.MULTILINE)
_TRUE_PEAK = re.compile(r"True peak:\s+Peak:\s+(-?\d+(?:\.\d+)?|-inf)\s+dBFS", re.MULTILINE)


def _number(pattern: re.Pattern[str], text: str) -> float | None:
    matches = pattern.findall(text)
    if not matches or matches[-1] == "-inf":
        return None
    return float(matches[-1])


class SignalQualityAnalyzer:
    def __init__(self, tool: FfmpegTool) -> None:
        self._tool = tool

    @property
    def identity(self) -> EngineIdentity:
        return EngineIdentity("signal-quality-analyzer", f"{self._tool.version}+r{_REVISION}")

    def analyze(self, audio: Path, cancellation: CancellationToken) -> QualityMeasurements:
        samples, rate = read_wav(audio)
        metrics = measure_signal(samples, rate)
        result = self._tool.run(
            [
                "-nostats",
                "-i",
                str(audio),
                "-af",
                "ebur128=peak=true:framelog=quiet",
                "-f",
                "null",
                "-",
            ],
            cancellation,
        )
        integrated = _number(_INTEGRATED, result.stderr)
        true_peak = _number(_TRUE_PEAK, result.stderr)
        if "Integrated loudness" not in result.stderr:
            raise ExternalSystemError(
                "FFmpeg returned no loudness summary", user_message="Loudness measurement failed."
            )
        measurable = integrated is not None and integrated > _GATED_LUFS
        return QualityMeasurements(
            duration=len(samples) / rate,
            sample_rate=rate,
            channels=samples.shape[1],
            integrated_lufs=integrated if measurable else None,
            loudness_range_lu=_number(_RANGE, result.stderr) if measurable else None,
            true_peak_dbtp=true_peak,
            sample_peak_dbfs=metrics.sample_peak_dbfs,
            rms_dbfs=metrics.rms_dbfs,
            speech_ratio=metrics.speech_ratio,
            speech_level_dbfs=metrics.speech_level_dbfs,
            noise_floor_dbfs=metrics.noise_floor_dbfs,
            snr_db=metrics.snr_db,
            hum_hz=metrics.hum_hz,
            hum_prominence_db=metrics.hum_prominence_db,
            noise_hf_share=metrics.noise_hf_share,
            clipping_ratio=metrics.clipping_ratio,
            crest_factor_db=metrics.crest_factor_db,
            speech_dynamics_db=metrics.speech_dynamics_db,
            sibilance_peak_db=metrics.sibilance_peak_db,
            rumble_db=metrics.rumble_db,
            mud_db=metrics.mud_db,
            harshness_db=metrics.harshness_db,
            reverb_rt60=metrics.reverb_rt60,
        )
