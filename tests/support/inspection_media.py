"""Real (tiny) media files with known technical properties, made once per test session.

Each builder states in its docstring what the file IS, so a test can assert the facts exactly.
"""

from collections.abc import Callable
from pathlib import Path

from tests.support.media_factories import ffmpeg, make_video, make_wav

_SIZE = "160x120"
_SETTB = "settb=1/1000,setpts='(N/30+0.01*mod(N,2))/TB'"


def _lavfi_video(rate: str, seconds: float = 2) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={_SIZE}:rate={rate}"]


def _lavfi_audio(rate: int = 48_000, seconds: float = 2, frequency: int = 440) -> list[str]:
    return [
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration={seconds}:sample_rate={rate}",
    ]


class Samples:
    """Builds each sample on first use; ``root`` is shared by the whole test session."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _build(self, name: str, build: Callable[[Path], object]) -> Path:
        path = self.root / name
        if not path.exists():
            build(path)
        return path

    # --- ordinary media -----------------------------------------------------------------------
    def cfr(self) -> Path:
        """H.264 29.97 fps (30000/1001), 160x120, AAC 48 kHz stereo, start timecode 01:02:03;04."""
        return self._build(
            "cfr.mp4",
            lambda p: ffmpeg(
                *_lavfi_video("30000/1001"),
                *_lavfi_audio(),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-timecode",
                "01:02:03;04",
                "-shortest",
                str(p),
            ),
        )

    def cfr_25(self) -> Path:
        """MPEG-4 part 2 at exactly 25 fps with AAC; non-drop timecode 10:00:00:00."""
        return self._build(
            "cfr25.mp4",
            lambda p: ffmpeg(
                *_lavfi_video("25"),
                *_lavfi_audio(44_100),
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                "-timecode",
                "10:00:00:00",
                "-shortest",
                str(p),
            ),
        )

    def vfr(self) -> Path:
        """Video only. 60 frames whose spacing alternates between 43 and 23 ms (variable rate)."""
        return self._build(
            "vfr.mkv",
            lambda p: ffmpeg(
                *_lavfi_video("30"),
                "-vf",
                _SETTB,
                "-fps_mode",
                "passthrough",
                "-enc_time_base",
                "1/1000",
                "-c:v",
                "libx264",
                str(p),
            ),
        )

    def dropped_frames(self) -> Path:
        """Video only, 30 fps, 4 s; frames 30 and 31 are missing (one gap of three intervals)."""
        return self._build(
            "dropped.mkv",
            lambda p: ffmpeg(
                *_lavfi_video("30", 4),
                "-vf",
                "select='not(between(n,30,31))'",
                "-fps_mode",
                "passthrough",
                "-c:v",
                "libx264",
                str(p),
            ),
        )

    def hole(self) -> Path:
        """Video only, 30 fps, 4 s; two seconds (frames 30-89) are missing in the middle."""
        return self._build(
            "hole.mkv",
            lambda p: ffmpeg(
                *_lavfi_video("30", 4),
                "-vf",
                "select='not(between(n,30,89))'",
                "-fps_mode",
                "passthrough",
                "-c:v",
                "libx264",
                str(p),
            ),
        )

    def hdr(self) -> Path:
        """HEVC Main10, BT.2020 / PQ, with mastering display and content light level; no audio."""
        params = (
            "log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:"
            "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):"
            "max-cll=1000,400:repeat-headers=1"
        )
        return self._build(
            "hdr.mp4",
            lambda p: ffmpeg(
                *_lavfi_video("24"),
                "-pix_fmt",
                "yuv420p10le",
                "-c:v",
                "libx265",
                "-x265-params",
                params,
                str(p),
            ),
        )

    def rotated(self) -> Path:
        """The ``cfr`` video with a display matrix of 90 degrees (counter-clockwise)."""
        source = self.cfr()
        return self._build(
            "rotated.mp4",
            lambda p: ffmpeg("-display_rotation", "90", "-i", str(source), "-c", "copy", str(p)),
        )

    def multi_audio(self) -> Path:
        """25 fps H.264 video with two AAC streams: 44.1 kHz mono and 22.05 kHz mono."""
        return self._build(
            "multi.mkv",
            lambda p: ffmpeg(
                *_lavfi_video("25"),
                *_lavfi_audio(44_100),
                *_lavfi_audio(22_050, frequency=880),
                "-map",
                "0",
                "-map",
                "1",
                "-map",
                "2",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-metadata:s:a:0",
                "language=eng",
                "-metadata:s:a:1",
                "language=deu",
                str(p),
            ),
        )

    def prores(self) -> Path:
        """ProRes 422 (10-bit 4:2:2) with 24-bit PCM audio in a MOV."""
        return self._build(
            "prores.mov",
            lambda p: ffmpeg(
                *_lavfi_video("24"),
                *_lavfi_audio(48_000),
                "-c:v",
                "prores_ks",
                "-profile:v",
                "2",
                "-pix_fmt",
                "yuv422p10le",
                "-c:a",
                "pcm_s24le",
                "-shortest",
                str(p),
            ),
        )

    def tagged(self) -> Path:
        """Matroska with camera and clip tags."""
        return self._build(
            "tagged.mkv",
            lambda p: ffmpeg(
                *_lavfi_video("25"),
                "-c:v",
                "libx264",
                "-metadata",
                "make=ACME",
                "-metadata",
                "model=Cam One",
                "-metadata",
                "creation_time=2026-03-04T05:06:07Z",
                "-metadata",
                "scene=12A",
                "-metadata",
                "take=3",
                str(p),
            ),
        )

    # --- audio / video only, offsets -----------------------------------------------------------
    def audio_only(self) -> Path:
        return self._build("voice.wav", lambda p: make_wav(p, seconds=2.0, rate=48_000, channels=2))

    def silent_video(self) -> Path:
        """MPEG-4 25 fps without any audio track."""
        return self._build("silent.mp4", lambda p: make_video(p, audio_delay=None))

    def late_audio(self) -> Path:
        """MPEG-4 25 fps whose AAC audio starts 0.5 s after the video."""
        return self._build("late.mp4", lambda p: make_video(p, audio_delay=0.5))

    def synced(self) -> Path:
        """MPEG-4 25 fps with audio starting together with the video."""
        return self._build("synced.mp4", lambda p: make_video(p, audio_delay=0.0))

    # --- damaged ---------------------------------------------------------------------------------
    def truncated_mkv(self) -> Path:
        """A readable Matroska file that stops in the middle of its data."""
        source = self.dropped_frames().read_bytes()
        return self._build("truncated.mkv", lambda p: p.write_bytes(source[: len(source) // 2]))

    def truncated_mp4(self) -> Path:
        """An MP4 whose index (moov atom) is missing: it cannot be opened at all."""
        source = self.cfr().read_bytes()
        return self._build("truncated.mp4", lambda p: p.write_bytes(source[: len(source) // 2]))

    def damaged_mkv(self) -> Path:
        """A Matroska file with a block of corrupted bytes inside the video data."""

        def build(path: Path) -> None:
            data = bytearray(self.dropped_frames().read_bytes())
            start = len(data) // 3
            for index in range(start, start + 400):
                data[index] ^= 0xFF
            path.write_bytes(bytes(data))

        return self._build("damaged.mkv", build)

    def not_media(self) -> Path:
        return self._build("junk.mp4", lambda p: p.write_bytes(b"not media at all" * 100))
