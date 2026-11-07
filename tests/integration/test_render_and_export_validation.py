"""The render manager's checks and the export validation against REAL files made by FFmpeg and read
by the real inspector and analyzer: a good export passes; a truncated file, a file without sound,
a wrong size, a wrong frame rate, black frames and a wrong duration are each caught.

Only the render backend is a stand-in (Resolve is not available): it "renders" by registering the
prepared file as the render's output, exactly as the Resolve adapter registers its render.
"""

import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.core.application.ports import MediaAnalyzer, ProcessRunner
from media_house.modules.export_validation.application.catalog import ValidationHistory
from media_house.modules.export_validation.application.store import ValidationStore
from media_house.modules.export_validation.application.validate import (
    IntentionalSpan,
    ValidateExport,
    ValidateExportCommand,
    ValidationOutcome,
)
from media_house.modules.export_validation.domain.values import Status
from media_house.modules.media_inspection.application.contracts import MediaInspector
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.render_manager.application.catalog import RenderHistory
from media_house.modules.render_manager.application.ports import (
    BackendCapabilities,
    BackendIdentity,
    BackendRequest,
    BackendResult,
)
from media_house.modules.render_manager.application.profiles import RenderProfileBook
from media_house.modules.render_manager.application.render import (
    RenderAuthorization,
    RenderOutcome,
    RenderRequest,
    RenderService,
)
from media_house.modules.render_manager.application.store import RenderStore
from media_house.modules.render_manager.domain.values import RenderType, Verification
from media_house.modules.render_manager.infrastructure.disk import LocalDiskProbe
from media_house.modules.render_manager.infrastructure.ffmpeg_cutter import FfmpegSectionCutter
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ApprovalKind,
    ArtifactKind,
    AuthorizeExportCommand,
    DecideCommand,
    OpenSessionCommand,
    ReviewState,
    Verdict,
)
from media_house.modules.review_orchestrator.application.orchestrator import ReviewOrchestrator
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from media_house.shared.events import EventBus
from tests.support.fakes import FixedClock
from tests.support.review_fakes import InMemoryReviewStore, StubTimelineCatalog
from tests.unit.timeline_builder import fixtures as bf

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg"),
]
CTX = JobContext.detached()
ADA, QC = Actor.human("Ada"), Actor.system("quality_control")
#: A 440 Hz tone at about -14 LUFS, the delivery's target.
_TONE = "sine=frequency=440:sample_rate=48000:duration={d},volume=2.44"


def make_clip(
    path: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 25,
    seconds: int = 8,
    audio: bool = True,
    black: tuple[int, int] | None = None,
    truncate: bool = False,
    comment: str = "",
) -> Path:
    video = f"testsrc2=size={width}x{height}:rate={fps}:duration={seconds}"
    graph = "[0:v]null[v]"
    if black is not None:
        window = f"between(t,{black[0]},{black[1]})"
        box = f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='{window}'"
        graph = f"[0:v]{box}[v]"
    args = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", video]
    if audio:
        args += ["-f", "lavfi", "-i", _TONE.format(d=seconds)]
    args += ["-filter_complex", graph, "-map", "[v]"]
    if audio:
        args += ["-map", "1:a", "-c:a", "aac", "-ac", "2", "-ar", "48000"]
    args += [
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv", "-movflags", "+faststart",
        "-metadata", f"comment={comment or path.stem}", str(path),
    ]  # fmt: skip
    subprocess.run(args, check=True)  # noqa: S603
    if truncate:
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
    return path


class SourceBackend:
    """Registers the prepared file as the render's output, like the Resolve adapter does."""

    def __init__(self, library: MediaLibrary) -> None:
        self._library = library
        self.source: Path | None = None

    def identity(self) -> BackendIdentity:
        return BackendIdentity(
            "test_backend", "1",
            BackendCapabilities(False, False, frozenset({"youtube", "master"}),
                                frozenset({"container", "codec", "audio_codec"})),
        )  # fmt: skip

    def render(self, request: BackendRequest, ctx: JobContext) -> BackendResult:
        assert self.source is not None
        done = self._library.register_derived(
            request.anchor_asset_id,
            self.source,
            operation="test_render",
            config={"name": request.file_name, "source": self.source.name},
            display_name=request.file_name,
        )
        assert isinstance(done, Ok), done
        return BackendResult("completed", done.value.asset)

    def pause(self) -> bool:
        return False

    def resume(self) -> bool:
        return False


@dataclass
class Real:
    """The real library, inspector and analyzer; a new review of each new timeline version."""

    app: Application
    anchor: str
    backend: SourceBackend
    tmp: Path
    counter: int = 0

    def world(self, label: str) -> "Scene":
        self.counter += 1
        timeline = bf.built()
        timeline = replace(
            timeline,
            anchor_asset_id=self.anchor,
            build=replace(timeline.build, build_hash=f"hash-{label}-{self.counter}"),
        )
        catalog = StubTimelineCatalog(timeline)
        container = self.app.container
        library = container.resolve(MediaLibrary)
        paths = container.resolve(
            __import__("media_house.shared.filesystem", fromlist=["AppPaths"]).AppPaths
        )
        sessions = ReviewOrchestrator(InMemoryReviewStore(), catalog, FixedClock(), EventBus())
        store = RenderStore(library, paths)
        inspector = container.resolve(MediaInspector)
        events = EventBus()
        clock = FixedClock()
        renders = RenderService(
            library, catalog, self.backend, FfmpegSectionCutter(container.resolve(ProcessRunner)),
            LocalDiskProbe(), inspector, sessions, store, paths, clock, events,
        )  # fmt: skip
        history = RenderHistory(store)
        profiles = RenderProfileBook(catalog)
        validator = ValidateExport(
            library, catalog, inspector, container.resolve(MediaAnalyzer), history, profiles,
            _NoMixes(), sessions, ValidationStore(library, paths), paths, clock, events,
        )  # fmt: skip
        return Scene(
            self,
            timeline,
            sessions,
            renders,
            validator,
            profiles,
            ValidationHistory(ValidationStore(library, paths)),
        )


class _NoMixes:
    def latest_result(self, anchor: str, timeline_id: str | None) -> Ok[None]:
        return Ok(None)

    def latest_spec(self, anchor: str, timeline_id: str | None) -> Ok[None]:
        return Ok(None)

    def find_spec(self, anchor: str, content_hash: str) -> Ok[None]:
        return Ok(None)


@dataclass
class Scene:
    real: Real
    timeline: object
    sessions: ReviewOrchestrator
    renders: RenderService
    validator: ValidateExport
    profiles: RenderProfileBook
    history: ValidationHistory

    def review(self, clip: Path) -> RenderOutcome:
        self.real.backend.source = clip
        done = self.renders.render(
            RenderRequest(self.real.anchor, RenderType.REVIEW, "review_fast"), CTX
        )
        assert isinstance(done, Ok), done
        return done.value

    def authorize(self) -> RenderAuthorization:
        opened = self.sessions.open(OpenSessionCommand(self.real.anchor, "Ada"))
        assert isinstance(opened, Ok)
        sid = opened.value.session.session_id
        ref = opened.value.session.timeline
        o = self.sessions
        o.advance(sid, ReviewState.ANALYSIS_PENDING, actor=QC)
        o.attach(sid, ArtifactKind.QUALITY_REPORT, "qr", timeline=ref, actor=QC)
        o.advance(sid, ReviewState.REVIEW_READY, actor=QC)
        o.advance(sid, ReviewState.FINAL_POLISH_PENDING, actor=QC)
        o.attach(sid, ArtifactKind.FINAL_POLISH_REPORT, "fp", timeline=ref, actor=QC)
        o.advance(sid, ReviewState.AWAITING_FINAL_APPROVAL, actor=QC)
        o.decide(DecideCommand(sid, ApprovalKind.FINAL, "final", Verdict.APPROVED, ADA, ref))
        config = self.profiles.config_hash("youtube_16_9", anchor_asset_id=self.real.anchor)
        assert isinstance(config, Ok)
        authorized = o.authorize_export(
            AuthorizeExportCommand(sid, ADA, ref, "youtube_16_9", config.value, "qr", "fp")
        )
        assert isinstance(authorized, Ok)
        return RenderAuthorization(sid, authorized.value.export_authorization_id, ref)

    def export(self, clip: Path) -> tuple[RenderOutcome, RenderAuthorization]:
        authorization = self.authorize()
        self.real.backend.source = clip
        done = self.renders.render(
            RenderRequest(
                self.real.anchor,
                RenderType.FINAL,
                "youtube_16_9",
                authorization=authorization,
                review_session_id=authorization.session_id,
            ),
            CTX,
        )
        assert isinstance(done, Ok), done
        return done.value, authorization

    def validate(
        self, clip: Path, reference: str, *, intentional: tuple[IntentionalSpan, ...] = ()
    ) -> tuple[RenderOutcome, ValidationOutcome]:
        exported, authorization = self.export(clip)
        assert exported.output is not None
        done = self.validator.execute(
            ValidateExportCommand(
                output_asset_id=exported.output.id,
                approved=authorization.timeline,
                delivery_id="youtube_16_9",
                review_session_id=authorization.session_id,
                export_id="exp",
                authorized_config_hash=exported.report.output_config_hash,
                reference_render_asset_id=reference,
                intentional=intentional,
            ),
            CTX,
        )
        assert isinstance(done, Ok), done
        return exported, done.value


KINDS: dict[str, dict[str, object]] = {
    "good": {},
    "truncated": {"truncate": True},
    "silent_file": {"audio": False},
    "small": {"width": 1280, "height": 720},
    "thirty": {"fps": 30},
    "black": {"black": (2, 3)},
    "short": {"seconds": 5},
}


class Clips:
    """A fresh file for every use: identical bytes would be one inspection, and each render of a
    test is its own file."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder
        self._count = 0

    def __getitem__(self, kind: str) -> Path:
        self._count += 1
        return make_clip(
            self._folder / f"{kind}-{self._count}.mp4",
            **KINDS.get(kind, {}),  # type: ignore[arg-type]
        )


@pytest.fixture(scope="module")
def clips(tmp_path_factory: pytest.TempPathFactory) -> Clips:
    return Clips(tmp_path_factory.mktemp("clips"))


@pytest.fixture(scope="module")
def real(tmp_path_factory: pytest.TempPathFactory, clips: Clips) -> Iterator[Real]:
    tmp = tmp_path_factory.mktemp("real")
    app = Application.start(StartupOptions(env={}, home=tmp / "home", environment=Environment.TEST))
    try:
        library = app.container.resolve(MediaLibrary)
        anchor = library.import_file(
            make_clip(tmp / "anchor.mp4", seconds=2), display_name="recording"
        )
        assert isinstance(anchor, Ok)
        yield Real(app, anchor.value.asset.id, SourceBackend(library), tmp)
    finally:
        app.shutdown()


@pytest.fixture(scope="module")
def reference(real: Real, clips: Clips) -> str:
    """The approved review render the exports are compared with."""
    outcome = real.world("reference").review(clips["good"])
    assert outcome.succeeded and outcome.output is not None
    return outcome.output.id


def codes(outcome: ValidationOutcome) -> set[str]:
    return {f.code for f in outcome.report.detected_problems}


# --- the render manager looks at the file ---------------------------------------------------------


def test_a_good_review_render_is_verified_from_the_real_file(real: Real, clips: Clips) -> None:
    outcome = real.world("verify-good").review(clips["good"])

    assert outcome.succeeded and outcome.report.verification_status is Verification.VERIFIED
    report = outcome.report
    assert (report.width, report.height, report.codec, report.container) == (
        1920,
        1080,
        "h264",
        "mp4",
    )
    assert report.audio is not None and report.audio.sample_rate == 48000
    assert report.frame_rate is not None and report.frame_rate.value == 25.0


@pytest.mark.parametrize(
    ("clip", "failed_check"),
    [
        ("silent_file", "audio stream"),
        ("small", "resolution"),
        ("thirty", "frame rate"),
        ("short", "duration"),
    ],
)
def test_a_completed_render_whose_file_is_wrong_is_not_a_success(
    real: Real, clips: Clips, clip: str, failed_check: str
) -> None:
    outcome = real.world(f"verify-{clip}").review(clips[clip])

    assert not outcome.succeeded
    assert outcome.report.verification_status is Verification.FAILED
    assert failed_check in {c.name for c in outcome.report.checks if c.state.value == "failed"}


def test_a_section_preview_is_really_cut_and_has_the_right_length(real: Real, clips: Clips) -> None:
    scene = real.world("preview")
    real.backend.source = clips["good"]
    done = scene.renders.render(
        RenderRequest(
            real.anchor, RenderType.SECTION_PREVIEW, "review_fast",
            start_frame=50, end_frame=100, padding_seconds=1.0,
        ),
        CTX,
    )  # fmt: skip

    assert isinstance(done, Ok) and done.value.succeeded
    assert (done.value.report.start_frame, done.value.report.end_frame) == (25, 125)
    assert done.value.output is not None and done.value.output.duration_seconds == pytest.approx(
        4.0, abs=0.2
    )


# --- export validation on the real exported file --------------------------------------------------


def test_a_good_export_passes_every_check(real: Real, clips: Clips, reference: str) -> None:
    exported, outcome = real.world("export-good").validate(clips["good"], reference)

    assert exported.succeeded
    assert outcome.status is Status.PASS, [
        (f.code, f.message) for f in outcome.report.detected_problems
    ]
    assert outcome.report.not_checked[0].mandatory is False  # only what pictures cannot show
    loudness = float(dict(outcome.report.measurements)["integrated_lufs"])
    assert -15.0 < loudness < -13.0


def test_a_truncated_export_fails_the_decode(real: Real, clips: Clips, reference: str) -> None:
    _, outcome = real.world("export-truncated").validate(clips["truncated"], reference)

    assert outcome.status is Status.FAIL
    assert codes(outcome) & {"file.decode_failed", "file.truncated", "file.unreadable"}


def test_an_export_without_sound_fails(real: Real, clips: Clips, reference: str) -> None:
    _, outcome = real.world("export-silent").validate(clips["silent_file"], reference)

    assert outcome.status is Status.FAIL and "audio.missing" in codes(outcome)


def test_an_export_of_the_wrong_size_and_rate_fails_and_goes_back_to_the_renderer(
    real: Real, clips: Clips, reference: str
) -> None:
    _, small = real.world("export-small").validate(clips["small"], reference)
    _, thirty = real.world("export-thirty").validate(clips["thirty"], reference)

    assert small.status is Status.FAIL and "video.resolution" in codes(small)
    assert thirty.status is Status.FAIL and "video.frame_rate" in codes(thirty)
    for finding in (*small.report.detected_problems, *thirty.report.detected_problems):
        if finding.code in {"video.resolution", "video.frame_rate"}:
            assert finding.route_to.value == "render_manager"
            assert finding.next_action.value == "rerender"


def test_black_frames_fail_unless_the_plan_declared_them(
    real: Real, clips: Clips, reference: str
) -> None:
    _, undeclared = real.world("export-black").validate(clips["black"], reference)
    _, declared = real.world("export-black-declared").validate(
        clips["black"], reference, intentional=(IntentionalSpan("black", 1.9, 3.1),)
    )

    assert undeclared.status is Status.FAIL and "video.black_frames" in codes(undeclared)
    assert declared.status is Status.PASS, [
        (f.code, f.message) for f in declared.report.detected_problems
    ]


def test_an_export_of_the_wrong_length_fails_against_the_approved_timeline(
    real: Real, clips: Clips, reference: str
) -> None:
    _, outcome = real.world("export-short").validate(clips["short"], reference)

    assert outcome.status is Status.FAIL and "timeline.duration" in codes(outcome)


def test_without_a_reference_render_the_real_file_still_needs_a_person(
    real: Real, clips: Clips
) -> None:
    _, outcome = real.world("export-noref").validate(clips["good"], "")

    assert outcome.status is Status.MANUAL_REVIEW_REQUIRED
    assert not codes(outcome)  # nothing is wrong; it just could not be compared
