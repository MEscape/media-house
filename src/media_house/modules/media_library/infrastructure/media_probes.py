"""Probes for audio/video (via ffprobe) and for typed JSON documents.

Both plug into ``FileMediaInspector`` exactly like the image probes: they decide by CONTENT
(never by file extension) and return ``None`` when the file is not their format.
"""

import json
import math
from pathlib import Path
from typing import Any

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.modules.media_library.domain.errors import InvalidMedia
from media_house.modules.media_library.domain.values import MediaType
from media_house.modules.media_library.infrastructure.image_probes import ProbeResult

_PROBE_TIMEOUT = 60.0
#: ffprobe "format_name" values of still-image containers: those are images, not video.
_IMAGE_FORMATS = frozenset({"image2", "gif", "apng", "webp_pipe"})

_WEBM_VIDEO = frozenset({"vp8", "vp9", "av1"})
_WEBM_AUDIO = frozenset({"opus", "vorbis"})

#: ``format_name`` token -> (extension, audio mime, video mime) for unambiguous containers.
_SIMPLE_FORMATS = {
    "wav": (".wav", "audio/wav", "video/x-wav"),
    "mp3": (".mp3", "audio/mpeg", "audio/mpeg"),
    "flac": (".flac", "audio/flac", "audio/flac"),
    "ogg": (".ogg", "audio/ogg", "video/ogg"),
    "aac": (".aac", "audio/aac", "audio/aac"),
    "aiff": (".aiff", "audio/aiff", "audio/aiff"),
    "avi": (".avi", "video/x-msvideo", "video/x-msvideo"),
}


def _float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class FfprobeProbe:
    """Audio and video files, described by ``ffprobe``. Put it AFTER the image probes.

    A missing ffprobe raises ``ToolNotFoundError`` (clear and actionable) from the runner.
    """

    def __init__(self, runner: ProcessRunner, *, executable: str = "ffprobe") -> None:
        self._runner = runner
        self._executable = executable

    def probe(self, path: Path, file_size: int) -> ProbeResult | None:
        _ = file_size
        result = self._runner.run(
            ProcessSpec(
                self._executable,
                ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                timeout_seconds=_PROBE_TIMEOUT,
            ),
        )
        if not result.succeeded:
            return None  # not something ffprobe understands
        try:
            info: dict[str, Any] = json.loads(result.stdout)
        except ValueError:
            return None
        streams: list[dict[str, Any]] = info.get("streams", [])
        container: dict[str, Any] = info.get("format", {})
        formats = set(str(container.get("format_name", "")).split(","))
        if formats & _IMAGE_FORMATS or any(f.endswith("_pipe") for f in formats):
            return None

        video = [
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ]
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        if not video and not audio:
            return None
        media_type = MediaType.VIDEO if video else MediaType.AUDIO
        classified = self._classify(formats, container, media_type, video, audio)
        if classified is None:
            return None
        extension, mime = classified

        duration = _float(container.get("duration"))
        if duration is None or duration <= 0:
            raise InvalidMedia(
                "Media has no usable duration",
                user_message="This audio or video file looks damaged or incomplete.",
            )
        first_video = video[0] if video else {}
        first_audio = audio[0] if audio else {}
        technical: dict[str, Any] = {
            "container": sorted(formats),
            "audio_stream_count": len(audio),
            "audio_codec": first_audio.get("codec_name"),
            "sample_rate": _float(first_audio.get("sample_rate")),
            "channels": first_audio.get("channels"),
            "video_codec": first_video.get("codec_name"),
            "bit_rate": _float(container.get("bit_rate")),
        }
        width = first_video.get("width")
        height = first_video.get("height")
        return ProbeResult(
            media_type=media_type,
            mime_type=mime,
            extension=extension,
            width=int(width) if isinstance(width, int) else None,
            height=int(height) if isinstance(height, int) else None,
            duration_seconds=duration,
            technical={k: v for k, v in technical.items() if v is not None},
        )

    @staticmethod
    def _classify(
        formats: set[str],
        container: dict[str, Any],
        media_type: MediaType,
        video: list[dict[str, Any]],
        audio: list[dict[str, Any]],
    ) -> tuple[str, str] | None:
        is_video = media_type is MediaType.VIDEO
        for token in sorted(formats):
            if token in _SIMPLE_FORMATS:
                extension, audio_mime, video_mime = _SIMPLE_FORMATS[token]
                return extension, video_mime if is_video else audio_mime
        if "mp4" in formats or "mov" in formats:
            brand = str(container.get("tags", {}).get("major_brand", "")).strip()
            if brand == "qt":
                return ".mov", "video/quicktime" if is_video else "audio/mp4"
            if not is_video:
                return ".m4a", "audio/mp4"
            return ".mp4", "video/mp4"
        if "matroska" in formats or "webm" in formats:
            video_codecs = {str(s.get("codec_name")) for s in video}
            audio_codecs = {str(s.get("codec_name")) for s in audio}
            webm = video_codecs <= _WEBM_VIDEO and audio_codecs <= _WEBM_AUDIO
            if is_video:
                return (".webm", "video/webm") if webm else (".mkv", "video/x-matroska")
            return (".weba", "audio/webm") if webm else (".mka", "audio/x-matroska")
        return None


class JsonDocumentProbe:
    """JSON objects that declare a ``document_type`` (e.g. transcripts) become ``OTHER`` assets.

    Requiring the marker keeps arbitrary JSON files out of the library.
    """

    #: Reading more than this into memory to sniff a file is refused.
    MAX_BYTES = 64 * 1024 * 1024

    def probe(self, path: Path, file_size: int) -> ProbeResult | None:
        if file_size > self.MAX_BYTES:
            return None
        try:
            with path.open("rb") as stream:
                head = stream.read(64).lstrip()
                if not head.startswith(b"{"):
                    return None
                stream.seek(0)
                document = json.loads(stream.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        kind = document.get("document_type") if isinstance(document, dict) else None
        if not isinstance(kind, str) or not kind:
            return None
        return ProbeResult(
            media_type=MediaType.OTHER,
            mime_type="application/json",
            extension=".json",
            technical={"document_type": kind},
        )
