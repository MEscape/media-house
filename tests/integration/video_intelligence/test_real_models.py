"""The real model engines on real pictures (scikit-image ships a portrait, a cat and a camera man).

Each test is skipped when the engine's library or model file is not available in this
environment (``engine.unavailable()`` says why), so the suite passes everywhere and proves the
adapters wherever the models are installed: ``uv sync --extra vision`` plus the model files in
``~/.cache/media-house/video-models`` (the MediaPipe task files) and the usual torch / Hugging
Face caches.
"""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from media_house.core.infrastructure.process import SubprocessRunner
from media_house.modules.video_intelligence.application.analyze_video import AnalyzeVideoCommand
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzerId,
    AnalyzerState,
    DeviceKind,
    EntityKind,
    OverlayKind,
    VideoAnalysis,
)
from media_house.modules.video_intelligence.application.ports import (
    DecodedVideo,
    DecodeRequest,
    MeasureRequest,
    SignalAnalyzer,
)
from media_house.modules.video_intelligence.domain.profiles import ProcessingProfile, get_profile
from media_house.modules.video_intelligence.domain.sampling import plan_rgb, rgb_stride
from media_house.modules.video_intelligence.domain.signals import (
    DetectionRow,
    DetectionSignals,
    EmbeddingSignals,
    ShotSignals,
)
from media_house.modules.video_intelligence.domain.source import SourceInfo
from media_house.modules.video_intelligence.infrastructure.clip_engines import (
    ClipAppearanceAnalyzer,
    ClipEmbedder,
    ClipRuntime,
)
from media_house.modules.video_intelligence.infrastructure.ffmpeg_frames import FfmpegFrameSource
from media_house.modules.video_intelligence.infrastructure.mediapipe_engines import (
    MediaPipeBodyAnalyzer,
    MediaPipeFaceAnalyzer,
)
from media_house.modules.video_intelligence.infrastructure.model_files import ModelStore
from media_house.modules.video_intelligence.infrastructure.numpy_signals import NumpyShotAnalyzer
from media_house.modules.video_intelligence.infrastructure.rapidocr_text import RapidOcrAnalyzer
from media_house.modules.video_intelligence.infrastructure.torch_support import cuda_available
from media_house.modules.video_intelligence.infrastructure.torchvision_detector import (
    TorchvisionDetector,
)
from media_house.modules.video_intelligence.infrastructure.vlm_descriptions import TransformersVlm
from media_house.modules.video_intelligence.module import classical_analyzers
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Ok
from tests.integration.video_intelligence.conftest import VideoEnv
from tests.support.video_media import write_clip

pytestmark = pytest.mark.integration

HOME = Path.home()
MODELS = ModelStore(
    HOME / ".cache" / "media-house" / "video-models",
    HOME / ".cache" / "torch" / "hub" / "checkpoints",
)
SIZE = (480, 360)  # width, height of the test pictures


def require(engine: SignalAnalyzer) -> SignalAnalyzer:
    why = engine.unavailable(False)
    if why is not None:
        pytest.skip(f"{engine.analyzer.value} is not available here: {why}")
    return engine


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (the numerator of the character error rate)."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def picture(name: str) -> np.ndarray:
    from skimage import data

    raw = {"astronaut": data.astronaut, "cat": data.chelsea}[name]()
    resized = Image.fromarray(raw).resize(SIZE, Image.Resampling.LANCZOS)
    return np.asarray(resized, dtype=np.float64) / 255.0


def two_shot_clip(venv: VideoEnv) -> str:
    """20 frames of the astronaut portrait, then 20 frames of a cat."""
    astronaut, cat = picture("astronaut"), picture("cat")
    path = write_clip(
        venv.media_dir / "portrait_and_cat.mp4",
        lambda i: astronaut if i < 20 else cat,
        frames=40,
        fps="30",
        audio=False,
    )
    return venv.import_clip(path)


def fast_models(*analyzers: AnalyzerId) -> ProcessingProfile:
    base = get_profile("standard")
    return replace(
        base,
        analyzers=(AnalyzerId.SHOTS, *analyzers),
        measurement=replace(
            base.measurement,
            rgb_fps=15.0,
            rgb_gap_seconds=0.2,
            rgb_width=480,
            text_every=1,
            description_every=1,
        ),
    )


def analyse(
    venv: VideoEnv, asset: str, chosen: ProcessingProfile, *engines: SignalAnalyzer
) -> VideoAnalysis:
    use = {**classical_analyzers(), **{e.analyzer: e for e in engines}}
    result = venv.build(use).execute(AnalyzeVideoCommand(asset, chosen), JobContext.detached())
    assert isinstance(result, Ok), result
    return result.value.analysis


class TestDetector:
    def test_a_person_and_a_cat_are_found_in_their_shots(self, venv: VideoEnv) -> None:
        detector = require(TorchvisionDetector(MODELS))

        analysis = analyse(venv, two_shot_clip(venv), fast_models(AnalyzerId.ENTITIES), detector)

        first, second = analysis.shots
        kinds = {t.shot_id: {(t.kind, t.label) for t in analysis.tracks} for t in analysis.tracks}
        assert any(
            t.kind is EntityKind.PERSON and t.shot_id == first.shot_id for t in analysis.tracks
        ), kinds
        assert any(t.label == "cat" and t.shot_id == second.shot_id for t in analysis.tracks), kinds
        assert first.framing.ok and first.composition.ok
        track = next(t for t in analysis.tracks if t.kind is EntityKind.PERSON)
        assert all(
            0 <= p.box.x0 < p.box.x1 <= 1 and 0 <= p.box.y0 < p.box.y1 <= 1 for p in track.points
        )

    def test_the_gpu_and_the_cpu_agree_within_tolerance(
        self, venv: VideoEnv, tmp_path: Path
    ) -> None:
        if not cuda_available():
            pytest.skip("no CUDA device")
        detector = require(TorchvisionDetector(MODELS))
        video, request = decode(venv, two_shot_clip(venv), tmp_path)

        cpu = detector.measure(video, replace(request, device=DeviceKind.CPU), CancellationToken())
        gpu = detector.measure(video, replace(request, device=DeviceKind.GPU), CancellationToken())

        assert isinstance(cpu, DetectionSignals) and isinstance(gpu, DetectionSignals)
        assert cpu.frames == gpu.frames and cpu.rows

        def strong(s: DetectionSignals) -> list[DetectionRow]:
            return [r for r in s.rows if r.confidence >= 0.6]

        for a, b in zip(strong(cpu), strong(gpu), strict=True):
            assert (a.frame, a.label) == (b.frame, b.label)
            assert abs(a.confidence - b.confidence) < 0.02
            assert max(abs(a.box.x0 - b.box.x0), abs(a.box.y0 - b.box.y0)) < 0.02
        assert detector.device(DeviceKind.GPU) == "gpu" and detector.device(DeviceKind.CPU) == "cpu"


def decode(venv: VideoEnv, asset: str, work: Path) -> tuple[DecodedVideo, MeasureRequest]:
    """Decode a clip once and build the request an analyzer gets from the use case."""
    path = venv.library.local_path(asset)
    assert isinstance(path, Ok)
    chosen = fast_models(AnalyzerId.ENTITIES)
    settings = chosen.measurement
    source = SourceInfo(
        width=SIZE[0],
        height=SIZE[1],
        frame_rate=None,
        duration_seconds=1.3,
        declared_frame_count=40,
        rotation=0,
        variable_frame_rate=False,
        color_transfer="bt709",
        color_range="tv",
    )
    source_frames = FfmpegFrameSource(SubprocessRunner())
    stride = rgb_stride(30.0, settings)
    video = source_frames.decode(
        path.value,
        DecodeRequest(source, settings, 2, stride, dense=True, samples=False, rgb=True),
        work,
        CancellationToken(),
    )
    shots = NumpyShotAnalyzer().measure(
        video,
        MeasureRequest(settings, 2, (), stride, (), None, source, {}, DeviceKind.CPU, False),
        CancellationToken(),
    )
    assert isinstance(shots, ShotSignals)
    request = MeasureRequest(
        settings,
        2,
        (),
        stride,
        plan_rgb(shots, stride, settings),
        shots,
        source,
        {},
        DeviceKind.AUTO,
        False,
    )
    return video, request


class TestFacesAndBody:
    def test_the_portrait_has_a_smiling_face_looking_roughly_at_the_camera(
        self, venv: VideoEnv
    ) -> None:
        faces = require(MediaPipeFaceAnalyzer(MODELS))

        analysis = analyse(venv, two_shot_clip(venv), fast_models(AnalyzerId.FACES), faces)

        portrait, cat = analysis.shots
        cues = portrait.faces
        assert cues.ok and cues.face_frames > 0
        assert cues.smile_cue is not None and cues.smile_cue >= 0.5
        assert cues.eyes_closed == 0.0
        assert cues.head_yaw is not None and abs(cues.head_yaw) < 35
        assert cues.mean_face_height is not None and 0.1 < cues.mean_face_height < 0.8
        assert cat.faces.reasons == ("no_usable_face_found",)

    def test_a_portrait_is_framed_as_a_close_or_medium_shot(self, venv: VideoEnv) -> None:
        faces, detector = (
            require(MediaPipeFaceAnalyzer(MODELS)),
            require(TorchvisionDetector(MODELS)),
        )

        analysis = analyse(
            venv,
            two_shot_clip(venv),
            fast_models(AnalyzerId.ENTITIES, AnalyzerId.FACES),
            detector,
            faces,
        )

        framing = analysis.shots[0].framing
        assert framing.ok and framing.framing is not None
        assert framing.framing.value in {"medium", "close_up", "extreme_close_up"}

    def test_body_pose_is_found_in_the_portrait(self, venv: VideoEnv) -> None:
        body = require(MediaPipeBodyAnalyzer(MODELS))

        analysis = analyse(venv, two_shot_clip(venv), fast_models(AnalyzerId.BODY), body)

        assert analysis.shots[0].body.ok
        assert (analysis.shots[0].body.pose_fraction or 0) > 0


class TestMeaning:
    def test_embeddings_separate_the_shots_and_label_them(self, venv: VideoEnv) -> None:
        clip = ClipRuntime()
        embedder = require(ClipEmbedder(clip))

        analysis = analyse(venv, two_shot_clip(venv), fast_models(AnalyzerId.EMBEDDINGS), embedder)

        portrait, cat = analysis.shots
        assert portrait.content.state in {AnalyzerState.OK, AnalyzerState.UNKNOWN}
        assert portrait.meaning.indoor_outdoor in {"indoor", "outdoor", None}
        assert cat.continuity.embedding_similarity is not None
        assert cat.continuity.embedding_similarity < 0.9  # a portrait and a cat are not alike
        vector = analysis.embedding(portrait.meaning.embedding_ref or "")
        assert vector is not None and abs(sum(v * v for v in vector.vector) - 1.0) < 1e-2
        assert len(analysis.scenes) == 2

    def test_the_gpu_and_the_cpu_embed_alike(self, venv: VideoEnv, tmp_path: Path) -> None:
        if not cuda_available():
            pytest.skip("no CUDA device")
        embedder = require(ClipEmbedder(ClipRuntime()))
        video, request = decode(venv, two_shot_clip(venv), tmp_path)

        cpu = ClipEmbedder(ClipRuntime()).measure(
            video, replace(request, device=DeviceKind.CPU), CancellationToken()
        )
        gpu = embedder.measure(video, replace(request, device=DeviceKind.GPU), CancellationToken())

        assert isinstance(cpu, EmbeddingSignals)
        assert isinstance(gpu, EmbeddingSignals)
        for a, b in zip(cpu.vectors, gpu.vectors, strict=True):
            assert sum(x * y for x, y in zip(a, b, strict=True)) > 0.999

    def test_people_get_an_anonymous_identity_from_their_appearance(self, venv: VideoEnv) -> None:
        detector = require(TorchvisionDetector(MODELS))
        appearance = require(ClipAppearanceAnalyzer(ClipRuntime()))

        analysis = analyse(
            venv,
            two_shot_clip(venv),
            fast_models(AnalyzerId.ENTITIES, AnalyzerId.APPEARANCE),
            detector,
            appearance,
        )

        assert [i.cluster_id for i in analysis.identities] == ["person_A"]
        assert all(
            t.identity_cluster == "person_A" for t in analysis.tracks if t.kind is EntityKind.PERSON
        )


class TestOnScreenText:
    def test_rendered_text_is_read_with_its_position(self, venv: VideoEnv) -> None:
        ocr = require(RapidOcrAnalyzer())
        sheet = Image.new("RGB", SIZE, "white")
        draw = ImageDraw.Draw(sheet)
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont
        try:
            font = ImageFont.truetype("arial.ttf", 48)
        except OSError:
            font = ImageFont.load_default(size=48)
        draw.text((40, 140), "HELLO WORLD 2026", fill="black", font=font)
        frame = np.asarray(sheet, dtype=np.float64) / 255.0
        path = write_clip(
            venv.media_dir / "text.mp4", lambda _i: frame, frames=20, fps="30", audio=False
        )

        analysis = analyse(venv, venv.import_clip(path), fast_models(AnalyzerId.TEXT), ocr)

        text = analysis.shots[0].text
        assert text.ok and text.items, text
        found = " ".join(i.text for i in text.items).lower()
        assert "hello" in found and "world" in found
        assert all(0 < i.box.y0 < i.box.y1 < 1 for i in text.items)

    def test_the_character_error_rate_on_clean_rendered_text_is_low(self, venv: VideoEnv) -> None:
        ocr = require(RapidOcrAnalyzer())
        expected = ["QUARTERLY REPORT", "Revenue grew 12 percent"]
        sheet = Image.new("RGB", SIZE, "white")
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default(size=40)
        for row, line in enumerate(expected):
            draw.text((20, 90 + 90 * row), line, fill="black", font=font)
        frame = np.asarray(sheet, dtype=np.float64) / 255.0
        path = write_clip(
            venv.media_dir / "report.mp4", lambda _i: frame, frames=15, fps="30", audio=False
        )

        analysis = analyse(venv, venv.import_clip(path), fast_models(AnalyzerId.TEXT), ocr)

        read = [i.text for i in sorted(analysis.shots[0].text.items, key=lambda i: i.box.y0)]
        errors = sum(
            edit_distance(a.lower(), b.lower()) for a, b in zip(expected, read, strict=False)
        )
        assert len(read) == len(expected), read
        assert errors / sum(len(e) for e in expected) <= 0.15, read

    def test_a_watermark_is_recognised_across_the_video(self, venv: VideoEnv) -> None:
        ocr = require(RapidOcrAnalyzer())
        astronaut = picture("astronaut")
        sheet = Image.fromarray((astronaut * 255).astype(np.uint8))
        ImageDraw.Draw(sheet).text(
            (330, 330), "@MEDIAHOUSE", fill="white", font=ImageFont.load_default(size=26)
        )
        marked = np.asarray(sheet, dtype=np.float64) / 255.0
        path = write_clip(
            venv.media_dir / "marked.mp4", lambda _i: marked, frames=30, fps="30", audio=False
        )

        analysis = analyse(venv, venv.import_clip(path), fast_models(AnalyzerId.TEXT), ocr)

        kinds = {o.kind for o in analysis.overlays}
        assert OverlayKind.WATERMARK in kinds, [o.text for o in analysis.overlays]


class TestDescriptions:
    def test_a_keyframe_is_described_by_the_vision_language_model(self, venv: VideoEnv) -> None:
        vlm = require(
            TransformersVlm(directory=HOME / ".cache" / "media-house" / "models" / "smolvlm")
        )

        analysis = analyse(venv, two_shot_clip(venv), fast_models(AnalyzerId.DESCRIPTIONS), vlm)

        described = [s.meaning.description for s in analysis.shots]
        assert all(d for d in described), described
        assert all(len(d) > 10 for d in described if d)


def test_model_engines_never_touch_the_network_unless_allowed() -> None:
    empty = ModelStore(Path("no-such-directory"))
    detector = TorchvisionDetector(empty)

    reason = detector.unavailable(allow_downloads=False)

    assert reason is not None and "ssdlite320" in reason and "allow model downloads" in reason
    assert detector.unavailable(allow_downloads=True) is None  # fetching is allowed, so it may run
