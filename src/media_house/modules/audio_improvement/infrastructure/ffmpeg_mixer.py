"""Voice over looped, ducked music through one FFmpeg filter graph.

music: looped, gained to its offset below the voice, ducked by the voice (sidechain), faded out
voice: untouched
mix:   exactly as long as the voice, in the voice's sample rate and channel count
"""

from pathlib import Path

from media_house.modules.audio_improvement.application.ports import EngineIdentity
from media_house.modules.audio_improvement.domain.settings import MixSettings
from media_house.modules.audio_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.shared.concurrency import CancellationToken

_REVISION = "1"
#: ``sidechaincompress`` accepts a linear threshold in this range.
_THRESHOLD_LIMITS = (0.000976563, 1.0)


class FfmpegMixer:
    """Structurally implements ``application.ports.AudioMixer``."""

    def __init__(self, tool: FfmpegTool) -> None:
        self._tool = tool

    @property
    def identity(self) -> EngineIdentity:
        return EngineIdentity("ffmpeg-sidechain-mixer", f"{self._tool.version}+r{_REVISION}")

    def mix(
        self,
        voice: Path,
        music: Path,
        destination: Path,
        settings: MixSettings,
        music_gain_db: float,
        cancellation: CancellationToken,
    ) -> None:
        facts = self._tool.probe_audio(voice, cancellation)
        threshold = min(
            _THRESHOLD_LIMITS[1], max(_THRESHOLD_LIMITS[0], 10 ** (settings.duck_threshold_db / 20))
        )
        fade_start = max(0.0, facts.duration - settings.music_fade_out_s)
        graph = (
            f"[0:a]asplit=2[voice][sidechain];"
            f"[1:a]volume={music_gain_db:.3f}dB,"
            f"afade=t=out:st={fade_start:.4f}:d={settings.music_fade_out_s:g}[music];"
            f"[music][sidechain]sidechaincompress=threshold={threshold:.6f}:ratio={settings.duck_ratio:g}"
            f":attack={settings.duck_attack_ms:g}:release={settings.duck_release_ms:g}[ducked];"
            f"[voice][ducked]amix=inputs=2:duration=first:normalize=0[mix]"
        )
        self._tool.run(
            [
                "-loglevel",
                "error",
                "-i",
                str(voice),
                "-stream_loop",
                "-1",
                "-i",
                str(music),
                "-filter_complex",
                graph,
                "-map",
                "[mix]",
                "-t",
                f"{facts.duration:.6f}",
                "-ar",
                str(facts.sample_rate),
                "-ac",
                str(facts.channels),
                "-c:a",
                "pcm_f32le",
                "-f",
                "wav",
                str(destination),
            ],
            cancellation,
        )
