"""Builders and scripted collaborators for proxy manager tests.

``facts`` builders describe a source, ``perfect_output`` the file a faithful encoder would make
for a spec, and the fakes let a test decide what the encoder, probe and disk do.
"""

import shutil
import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from media_house.modules.proxy_manager.application.ports import EncodeOutcome, EncodeRequest
from media_house.modules.proxy_manager.domain.source import (
    ORIGIN_PROBE,
    AudioFacts,
    SourceFacts,
    VideoFacts,
)
from media_house.modules.proxy_manager.domain.spec import ResolvedSpec
from media_house.modules.proxy_manager.domain.validation import (
    OutputAudio,
    OutputFacts,
    OutputVideo,
)
from media_house.shared.concurrency import CancellationToken

DEFAULT_SIZE = 50_000


def video_facts(
    *,
    width: int = 1920,
    height: int = 1080,
    sar: Fraction = Fraction(1),
    rotation: int = 0,
    seconds: float = 4.0,
    fps: int = 25,
    first_pts: float = 0.0,
    index: int = 0,
    variable: bool = False,
) -> VideoFacts:
    return VideoFacts(
        index=index,
        width=width,
        height=height,
        sample_aspect_ratio=sar,
        rotation=rotation,
        visible_frames=round(seconds * fps),
        first_pts=first_pts,
        end_pts=first_pts + seconds,
        frame_interval=1 / fps,
        variable_frame_rate=variable,
    )


def audio_facts(
    *,
    index: int = 1,
    channels: int = 2,
    rate: int = 48_000,
    seconds: float = 4.0,
    first: float = 0.0,
) -> AudioFacts:
    return AudioFacts(index, "aac", channels, rate, first, first + seconds)


def source_facts(
    video: VideoFacts | None = None,
    audio: Sequence[AudioFacts] | None = None,
    *,
    timecode: str | None = None,
) -> SourceFacts:
    return SourceFacts(
        video=video or video_facts(),
        audio=tuple(audio if audio is not None else (audio_facts(),)),
        timecode=timecode,
        origin=ORIGIN_PROBE,
    )


def perfect_output(
    spec: ResolvedSpec, facts: SourceFacts, *, size: int = DEFAULT_SIZE
) -> OutputFacts:
    """The facts of a file that keeps every promise of ``spec`` for ``facts``."""
    src = facts.require_video()
    rebased = bool(spec.transformations)
    first = 0.0 if rebased else src.first_pts
    codec = "h264" if spec.codec.value == "h264" else "prores"
    video = OutputVideo(
        codec=codec,
        width=spec.width,
        height=spec.height,
        sample_aspect_ratio=Fraction(1),
        rotation=spec.rotation,
        pixel_format=spec.pixel_format,
        frames=src.visible_frames,
        first_pts=first,
        end_pts=first + (src.end_pts - src.first_pts),
        frame_interval=src.frame_interval,
    )
    shift = first - src.first_pts
    by_index = {a.index: a for a in facts.audio}
    audio = tuple(
        OutputAudio(
            a.codec,
            a.channels,
            a.sample_rate,
            by_index[a.source_index].first_pts + shift,
            by_index[a.source_index].end_pts + shift,
            by_index[a.source_index].language,
        )
        for a in spec.audio
    )
    return OutputFacts(
        size_bytes=size,
        video=video,
        audio=audio,
        other_streams=0,
        timecode=facts.timecode,
        decode_errors=None,
    )


class CopyEncoder:
    """Stands in for FFmpeg: copies a prepared valid media file to the destination.

    ``on_encode(call_number)`` runs first, so a test can block, fail or cancel a given call.
    """

    def __init__(self, template: Path, on_encode: Callable[[int], None] | None = None) -> None:
        self._template = template
        self._on_encode = on_encode
        self.calls: list[EncodeRequest] = []
        self.by_destination: dict[Path, EncodeRequest] = {}
        self._lock = threading.Lock()

    @property
    def identity(self) -> str:
        return "fake-ffmpeg 1"

    def encode(
        self,
        request: EncodeRequest,
        cancellation: CancellationToken,
        on_progress: Callable[[float], None],
    ) -> EncodeOutcome:
        with self._lock:
            self.calls.append(request)
            self.by_destination[request.destination] = request
            number = len(self.calls)
        if self._on_encode is not None:
            self._on_encode(number)
        cancellation.raise_if_cancelled()
        shutil.copyfile(self._template, request.destination)
        on_progress(1.0)
        return EncodeOutcome(encoder="fake-encoder")


class ScriptedProbe:
    """Returns the facts a test scripted instead of reading files."""

    def __init__(
        self,
        facts: SourceFacts | None,
        output: Callable[[Path, bool], OutputFacts],
    ) -> None:
        self.facts = facts
        self._output = output
        self.source_probes = 0
        self.output_probes: list[tuple[Path, bool]] = []

    def probe_source(self, path: Path, cancellation: CancellationToken) -> SourceFacts:
        self.source_probes += 1
        assert self.facts is not None, "this probe was not given source facts"
        return self.facts

    def probe_output(
        self, path: Path, cancellation: CancellationToken, *, decode: bool
    ) -> OutputFacts:
        self.output_probes.append((path, decode))
        return self._output(path, decode)


class FakeDisk:
    """Free space a test controls; sizes are real."""

    def __init__(self, free: int = 10**13) -> None:
        self.free = free

    def free_bytes(self, path: Path) -> int:
        return self.free

    def volume_of(self, path: Path) -> str:
        return "volume-1"

    def size_bytes(self, path: Path) -> int | None:
        return path.stat().st_size if path.is_file() else None


def faithful_probe(encoder: CopyEncoder) -> ScriptedProbe:
    """A probe that reports every file as exactly what the encoder was asked to make."""

    def output(path: Path, decode: bool) -> OutputFacts:
        request = encoder.by_destination.get(path) or encoder.calls[-1]
        made = perfect_output(request.spec, request.facts)
        return replace(made, decode_errors=0) if decode else made

    return ScriptedProbe(None, output)


class Gate:
    """Blocks one encode call until the test lets it go."""

    def __init__(self, blocked_call: int) -> None:
        self._call = blocked_call
        self.inside = threading.Event()
        self.release = threading.Event()

    def __call__(self, call: int) -> None:
        if call == self._call:
            self.inside.set()
            assert self.release.wait(30), "the test never released the encoder"


def in_thread(run: Callable[[], object]) -> tuple[threading.Thread, dict[str, object]]:
    box: dict[str, object] = {}

    def target() -> None:
        try:
            box["result"] = run()
        except BaseException as exc:  # noqa: BLE001 - handed to the asserting test
            box["error"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    return thread, box


class WaitSignal:
    """A progress reporter that tells a test when a call reached a given stage."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.reached = threading.Event()

    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        if self._text in message:
            self.reached.set()
