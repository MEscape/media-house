"""Doubles shared by the render manager and export validation tests: a small library that keeps
real files, an inspector that answers from the timeline's own settings, an analyzer with canned
measurements, and a render backend that writes a file and registers it like the real one."""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import cast

from media_house.core.application.ports import (
    AnalyzerCapabilities,
    DecodeReport,
    FrameComparison,
    FrameSample,
    LoudnessReading,
    TimeSpan,
)
from media_house.core.domain import Rational
from media_house.modules.media_inspection.application.contracts import (
    InspectionResult,
    InspectMediaCommand,
    MediaInspection,
    Sourced,
)
from media_house.modules.media_library.application.contracts import (
    JsonValue,
    MediaAssetDto,
    MediaLibrary,
)
from media_house.modules.media_library.application.dto import DerivationDto, DerivedAssetResultDto
from media_house.modules.render_manager.application.ports import (
    BackendCapabilities,
    BackendIdentity,
    BackendRequest,
    BackendResult,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimeline
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, MediaHouseError, NotFoundError, Ok
from tests.support import inspection_fakes as inf
from tests.support.asset_dto import make_asset


class FileLibrary:
    """The part of the media library the Layer 6 modules use, keeping real files in a folder."""

    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)
        self.assets: dict[str, MediaAssetDto] = {}
        self.files: dict[str, Path] = {}
        self._derived: dict[tuple[str, str], str] = {}
        self._counter = 0

    def contract(self) -> MediaLibrary:
        """This fake where a ``MediaLibrary`` is expected (it implements the part they use)."""
        return cast("MediaLibrary", self)

    def add_original(
        self, asset_id: str, path: Path | None = None, **changes: object
    ) -> MediaAssetDto:
        asset = make_asset(asset_id, **changes)
        self.assets[asset_id] = asset
        if path is not None:
            self.files[asset_id] = path
        return asset

    def add_file(
        self, asset_id: str, path: Path, *, derived_from: str | None = None, **changes: object
    ) -> MediaAssetDto:
        data = path.read_bytes() if path.exists() else b""
        asset = replace(
            make_asset(asset_id),
            extension=path.suffix,
            original_filename=path.name,
            display_name=path.stem,
            file_size=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            **changes,  # type: ignore[arg-type]
        )
        self.assets[asset_id] = asset
        self.files[asset_id] = path
        return asset

    # --- the MediaLibrary subset -----------------------------------------------------------------
    def get(self, asset_id: str) -> Ok[MediaAssetDto] | Err[NotFoundError]:
        found = self.assets.get(asset_id)
        return Ok(found) if found else Err(NotFoundError(asset_id))

    def local_path(self, asset_id: str) -> Ok[Path] | Err[NotFoundError]:
        path = self.files.get(asset_id)
        return Ok(path) if path is not None and path.exists() else Err(NotFoundError(asset_id))

    def fingerprint(
        self, source: str, operation: str, config: Mapping[str, JsonValue], version: int = 1
    ) -> Ok[str] | Err[NotFoundError]:
        if source not in self.assets:
            return Err(NotFoundError(source))
        text = json.dumps([source, operation, config, version], sort_keys=True, default=str)
        return Ok(hashlib.sha256(text.encode()).hexdigest())

    def find_derived_asset(self, source: str, fingerprint: str) -> MediaAssetDto | None:
        found = self._derived.get((source, fingerprint))
        return self.assets.get(found) if found else None

    def register_derived(
        self,
        source: str,
        path: Path,
        *,
        operation: str,
        config: Mapping[str, JsonValue],
        version: int = 1,
        display_name: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
        **_: object,
    ) -> Ok[DerivedAssetResultDto] | Err[NotFoundError]:
        fingerprint = self.fingerprint(source, operation, config, version)
        if isinstance(fingerprint, Err):
            return fingerprint
        existing = self.find_derived_asset(source, fingerprint.value)
        if existing is not None:
            return Ok(DerivedAssetResultDto(existing, created=False))
        self._counter += 1
        asset_id = f"derived-{self._counter}"
        stored = self._root / f"{asset_id}{path.suffix}"
        stored.write_bytes(path.read_bytes())
        asset = replace(
            self.add_file(asset_id, stored),
            display_name=display_name or stored.stem,
            metadata={
                "processing_type": operation,
                "source_asset_id": source,
                **dict(metadata or {}),
            },
            derivation=DerivationDto(source, operation, version, dict(config), fingerprint.value),
        )
        self.assets[asset_id] = asset
        self._derived[(source, fingerprint.value)] = asset_id
        return Ok(DerivedAssetResultDto(asset, created=True))

    def list_derived(self, source: str) -> list[MediaAssetDto]:
        return [
            self.assets[a]
            for (s, _), a in self._derived.items()
            if s == source and a in self.assets
        ]

    def delete(self, asset_id: str, *, include_derived: bool = True) -> Ok[None]:
        self.assets.pop(asset_id, None)
        return Ok(None)


def timeline_inspection(
    timeline: BuiltTimeline,
    *,
    duration: float | None = None,
    audio: bool = True,
    width: int | None = None,
    height: int | None = None,
    video_codec: str = "h264",
    audio_codec: str = "aac",
    frame_rate: Rational | None = None,
    pixel_format: str = "yuv420p",
    readable: bool = True,
    **audio_changes: object,
) -> MediaInspection:
    """The inspection of a file that is what the timeline asks for (override to break it)."""
    s = timeline.settings
    seconds = (
        duration
        if duration is not None
        else s.duration_frames * s.frame_rate.denominator / s.frame_rate.numerator
    )
    rate = frame_rate or s.frame_rate
    w, h = width or s.width, height or s.height
    base = inf.video()
    geometry = replace(
        base.geometry,
        width=w,
        height=h,
        display_aspect_ratio=Sourced.declared(Rational(w, h)),
    )
    vstream = replace(
        base,
        codec=video_codec,
        geometry=geometry,
        frame_rate=Sourced.declared(rate),
        average_frame_rate=Sourced.declared(rate),
        pixel_format=pixel_format,
        duration=seconds,
        declared_frame_count=round(seconds * rate.value),
        timing=inf.timing(packet_count=round(seconds * rate.value), end_pts=seconds),
    )
    astreams = (inf.audio(codec=audio_codec, duration=seconds, **audio_changes),) if audio else ()
    media = inf.observed(
        (vstream,),
        astreams,
        container=replace(
            inf.observed().container,
            duration=seconds,
            tags={"title": "A video"},
            stream_count=1 + len(astreams),
        ),
        integrity=replace(inf.observed().integrity, readable=readable),
    )
    return inf.inspect(media)


@dataclass
class FakeInspector:
    """Answers ``InspectMediaCommand`` from a table; unknown assets get ``default``."""

    default: Callable[[MediaAssetDto], MediaInspection | None]
    answers: dict[str, MediaInspection | None] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    #: Where the real asset (with its derivation) is looked up.
    library: FileLibrary | None = None
    #: Raised by every call (the inspection is unavailable).
    raises: MediaHouseError | None = None

    def execute(
        self, command: InspectMediaCommand, ctx: JobContext
    ) -> Ok[InspectionResult] | Err[NotFoundError]:
        self.calls.append(command.source_asset_id)
        if self.raises is not None:
            raise self.raises
        known = self.library.assets.get(command.source_asset_id) if self.library else None
        asset = known or make_asset(command.source_asset_id)
        inspection = (
            self.answers[command.source_asset_id]
            if command.source_asset_id in self.answers
            else self.default(asset)
        )
        if inspection is None:
            return Err(NotFoundError(command.source_asset_id))
        return Ok(InspectionResult(inspection, asset, created=True))

    def find(self, asset_id: str, config: object = None) -> InspectionResult | None:
        return None


class FakeAnalyzer:
    """A ``MediaAnalyzer`` with canned, adjustable measurements; pictures are tiny text files whose
    content is the file's "look" at that moment (so two looks are equal or not)."""

    def __init__(self) -> None:
        self.identity = "fake-analyzer r1"
        self.operations = {
            "black_spans", "frozen_spans", "silent_spans", "loudness", "decode",
            "sample_frames", "compare_frames",
        }  # fmt: skip
        self.black: tuple[TimeSpan, ...] = ()
        self.frozen: tuple[TimeSpan, ...] = ()
        self.silent: tuple[TimeSpan, ...] = ()
        self.loudness_reading = LoudnessReading(-14.0, 6.0, -2.0, -4.0, 384000, 48000, 2, "aac")
        self.decode_report = DecodeReport(True, 200, Fraction(8), ())
        #: per file: look at a moment (default: "same")
        self.looks: dict[Path, Callable[[Fraction], str]] = {}
        self.undecodable: set[Fraction] = set()
        self.calls: list[str] = []

    def capabilities(self) -> AnalyzerCapabilities:
        return AnalyzerCapabilities(self.identity, frozenset(self.operations))

    def black_spans(self, path: Path, **_: object) -> tuple[TimeSpan, ...]:
        self.calls.append("black")
        return self.black

    def frozen_spans(self, path: Path, **_: object) -> tuple[TimeSpan, ...]:
        self.calls.append("frozen")
        return self.frozen

    def silent_spans(self, path: Path, **_: object) -> tuple[TimeSpan, ...]:
        self.calls.append("silent")
        return self.silent

    def loudness(
        self, path: Path, *, span: TimeSpan | None, cancellation: CancellationToken
    ) -> LoudnessReading:
        self.calls.append("loudness")
        return self.loudness_reading

    def decode(self, path: Path, *, cancellation: CancellationToken) -> DecodeReport:
        self.calls.append("decode")
        return self.decode_report

    def sample_frames(
        self,
        path: Path,
        times: Sequence[Fraction],
        *,
        into: Path | None,
        cancellation: CancellationToken,
    ) -> tuple[FrameSample, ...]:
        self.calls.append("frames")
        assert into is not None
        into.mkdir(parents=True, exist_ok=True)
        made = []
        for index, moment in enumerate(times):
            if moment in self.undecodable:
                continue
            target = into / f"{index}.png"
            look = self.looks.get(path)
            target.write_text(look(moment) if look else "same", encoding="utf-8")
            made.append(
                FrameSample(
                    moment, target, 1920, 1080, 100.0, 10.0, 200.0, 0.0, 0.0,
                    (100.0, 100.0, 100.0), 0.3, 50.0, (0.0, 0.0, 0.0, 0.0), 0.0,
                )
            )  # fmt: skip
        return tuple(made)

    def compare_frames(self, a: Path, b: Path) -> FrameComparison:
        same = a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")
        return FrameComparison(1.0 if same else 0.2, 1.0 if same else 0.0, 0.0 if same else 200.0)


class FakeBackend:
    """A render backend that writes a file, registers it as a derived asset of the recording like
    the Resolve adapter does, and can be told to fail, be cancelled or lack a capability."""

    def __init__(self, library: FileLibrary, root: Path, *, name: str = "fake_resolve") -> None:
        self._library = library
        self._root = root
        self.name = name
        self.capabilities = BackendCapabilities(
            range_render=False,
            pause_resume=False,
            profiles=frozenset({"youtube", "master"}),
            applies=frozenset({"container", "codec", "audio_codec"}),
        )
        self.state = "completed"
        self.errors: tuple[str, ...] = ()
        self.extension = ".mp4"
        self.payload = b"\x00" * 8192
        self.requests: list[BackendRequest] = []
        self.paused = False
        self.on_render: Callable[[BackendRequest], None] | None = None

    def identity(self) -> BackendIdentity:
        return BackendIdentity(self.name, "19.0 (fake)", self.capabilities)

    def render(self, request: BackendRequest, ctx: JobContext) -> BackendResult:
        self.requests.append(request)
        if self.on_render is not None:
            self.on_render(request)
        ctx.raise_if_cancelled()
        if self.state != "completed":
            return BackendResult(self.state, None, errors=self.errors or ("the render failed",))
        source = self._root / f"{request.file_name}{self.extension}"
        source.write_bytes(self.payload)
        registered = self._library.register_derived(
            request.anchor_asset_id,
            source,
            operation="resolve_render",
            config={"name": request.file_name, "native": request.native_profile},
        )
        assert isinstance(registered, Ok)
        return BackendResult("completed", registered.value.asset)

    def pause(self) -> bool:
        self.paused = True
        return self.capabilities.pause_resume

    def resume(self) -> bool:
        self.paused = False
        return self.capabilities.pause_resume


class FakeCutter:
    def __init__(self) -> None:
        self.cuts: list[tuple[float, float]] = []

    def cut(
        self,
        source: Path,
        *,
        start_seconds: float,
        end_seconds: float,
        destination: Path,
        ctx: JobContext,
    ) -> None:
        self.cuts.append((start_seconds, end_seconds))
        destination.write_bytes(b"\x01" * 4096)


class FakeDisk:
    def __init__(self) -> None:
        self.value: int | None = 10_000_000_000

    def free_bytes(self, path: Path) -> int | None:
        return self.value
