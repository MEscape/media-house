"""The whole analysis with model-based analyzers, using fake engines behind the real ports.

Real FFmpeg decodes real clips once; the engines are deterministic fakes, so these tests check the
ARCHITECTURE: one decode for all streams, dependencies and tiering, per-analyzer caching and
invalidation through dependency fingerprints, graceful unavailability that cascades, the device
report, and the finished result (tracks, scenes, relations, scores, events).
"""

from dataclasses import replace

import pytest

from media_house.modules.video_intelligence.application.analyze_video import (
    AnalyzeVideoCommand,
    VideoAnalysisResult,
)
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzerId,
    AnalyzerState,
    CacheOutcome,
    DeviceKind,
    EventKind,
    RuntimeConfig,
    ScoreName,
    VideoAnalysis,
    analysis_from_json,
    validate_analysis,
)
from media_house.modules.video_intelligence.application.ports import SignalAnalyzer
from media_house.modules.video_intelligence.domain.profiles import ProcessingProfile
from media_house.modules.video_intelligence.module import classical_analyzers
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from tests.integration.video_intelligence.conftest import VideoEnv
from tests.support import vi_footage as vf
from tests.support.vi_engines import (
    FakeAppearance,
    FakeDetector,
    FakeEmbedder,
    FakeFaces,
)

pytestmark = pytest.mark.integration

MODEL_ANALYZERS = (
    AnalyzerId.SHOTS,
    AnalyzerId.QUALITY,
    AnalyzerId.MOTION,
    AnalyzerId.SALIENCY,
    AnalyzerId.GEOMETRY,
    AnalyzerId.ENTITIES,
    AnalyzerId.FACES,
    AnalyzerId.EMBEDDINGS,
    AnalyzerId.APPEARANCE,
)


def profile(*analyzers: AnalyzerId) -> ProcessingProfile:
    from media_house.modules.video_intelligence.domain.profiles import get_profile

    return replace(get_profile("standard"), analyzers=analyzers or MODEL_ANALYZERS)


def engines(**overrides: SignalAnalyzer) -> dict[AnalyzerId, SignalAnalyzer]:
    fakes: dict[AnalyzerId, SignalAnalyzer] = {
        AnalyzerId.ENTITIES: FakeDetector(),
        AnalyzerId.FACES: FakeFaces(),
        AnalyzerId.EMBEDDINGS: FakeEmbedder(),
        AnalyzerId.APPEARANCE: FakeAppearance(),
    }
    for key, value in overrides.items():
        fakes[AnalyzerId(key)] = value
    return {**classical_analyzers(), **fakes}


def clip(venv: VideoEnv, name: str = "clip") -> str:
    """Two shots of different content: a still scene, then a panning one (90 frames)."""
    frame, total, _ = vf.edit([vf.Segment(vf.still(1), 45), vf.Segment(vf.pan(2, 2.0), 45)])
    return venv.import_clip(vf.write(venv.media_dir / f"{name}.mp4", frame, total))


def run(
    venv: VideoEnv,
    asset: str,
    chosen: ProcessingProfile,
    use: dict[AnalyzerId, SignalAnalyzer] | None = None,
    runtime: RuntimeConfig | None = None,
) -> VideoAnalysisResult:
    command = AnalyzeVideoCommand(asset, chosen, runtime or RuntimeConfig())
    result = venv.build(use or engines()).execute(command, JobContext.detached())
    assert isinstance(result, Ok), result
    return result.value


def states(result: VideoAnalysisResult) -> dict[AnalyzerId, AnalyzerState]:
    return {r.analyzer: r.state for r in result.analysis.analyzers}


def cache(result: VideoAnalysisResult) -> dict[AnalyzerId, CacheOutcome]:
    return {r.analyzer: r.cache for r in result.analysis.analyzers}


class TestOneDecodeFansOut:
    def test_every_stream_comes_from_a_single_decode(self, venv: VideoEnv) -> None:
        run(venv, clip(venv), profile())

        assert venv.decodes() == 1
        decode = next(a for t, a in venv.runner.calls if t == "ffmpeg" and "-filter_complex" in a)
        graph = " ".join(decode)
        assert "[dense]" in graph and "[samples]" in graph and "[rgb]" in graph

    def test_the_colour_stream_is_only_decoded_when_an_analyzer_needs_it(
        self, venv: VideoEnv
    ) -> None:
        run(venv, clip(venv), profile(AnalyzerId.SHOTS, AnalyzerId.QUALITY))

        decode = next(a for t, a in venv.runner.calls if t == "ffmpeg" and "-filter_complex" in a)
        assert "[rgb]" not in " ".join(decode)

    def test_upgrading_decodes_only_the_stream_the_missing_analyzers_read(
        self, venv: VideoEnv
    ) -> None:
        asset = clip(venv)
        run(venv, asset, profile(AnalyzerId.SHOTS, AnalyzerId.QUALITY, AnalyzerId.MOTION))

        upgraded = run(venv, asset, profile())

        assert cache(upgraded)[AnalyzerId.SHOTS] is CacheOutcome.REUSED
        assert cache(upgraded)[AnalyzerId.QUALITY] is CacheOutcome.REUSED
        assert cache(upgraded)[AnalyzerId.ENTITIES] is CacheOutcome.COMPUTED
        last = [a for t, a in venv.runner.calls if t == "ffmpeg" and "-filter_complex" in a][-1]
        graph = " ".join(last)
        assert "[rgb]" in graph and "[dense]" not in graph  # the shots are not decoded again


class TestTheFinishedResult:
    def result(self, venv: VideoEnv) -> VideoAnalysisResult:
        return run(venv, clip(venv), profile())

    def test_every_analyzer_ran_and_the_result_is_valid_and_survives_storage(
        self, venv: VideoEnv
    ) -> None:
        result = self.result(venv)

        assert set(states(result).values()) == {AnalyzerState.OK}
        assert validate_analysis(result.analysis) == ()
        path = venv.library.local_path(result.asset.id)
        assert isinstance(path, Ok)
        assert analysis_from_json(path.value.read_text(encoding="utf-8")) == result.analysis

    def test_entities_become_tracks_per_shot_with_a_main_subject(self, venv: VideoEnv) -> None:
        analysis: VideoAnalysis = self.result(venv).analysis

        assert len(analysis.tracks) == len(analysis.shots) == 2
        for shot in analysis.shots:
            assert shot.entities.ok and shot.entities.main_subject_track_id is not None
            assert shot.framing.ok and shot.composition.ok

    def test_faces_and_appearance_feed_cues_and_anonymous_identities(self, venv: VideoEnv) -> None:
        analysis = self.result(venv).analysis

        assert all(s.faces.ok and s.faces.face_frames > 0 for s in analysis.shots)
        assert [i.cluster_id for i in analysis.identities] == [
            "person_A"
        ]  # the same look in both shots
        assert {t.identity_cluster for t in analysis.tracks} == {"person_A"}

    def test_different_pictures_are_different_scenes_and_shots_carry_embeddings(
        self, venv: VideoEnv
    ) -> None:
        analysis = self.result(venv).analysis

        assert len(analysis.scenes) == 2
        assert {s.scene_id for s in analysis.shots} == {s.scene_id for s in analysis.scenes}
        assert all(
            s.meaning.embedding_ref and analysis.embedding(s.meaning.embedding_ref)
            for s in analysis.shots
        )
        assert analysis.shots[1].continuity.ok
        assert analysis.shots[1].continuity.embedding_similarity is not None

    def test_scores_events_curves_and_geometry_are_present(self, venv: VideoEnv) -> None:
        analysis = self.result(venv).analysis

        shot = analysis.shots[0]
        assert [s.name for s in shot.scores] == list(ScoreName)
        assert shot.geometry.state in {AnalyzerState.OK, AnalyzerState.UNKNOWN}
        assert shot.lighting.ok and shot.lighting.color_balance is not None
        assert {r.aspect for r in shot.crop_safe} == {"16:9", "9:16", "1:1"}
        kinds = {e.kind for e in analysis.events}
        assert EventKind.SHOT_BOUNDARY in kinds
        assert {"mouth_activity"} <= {c.name for c in analysis.curves}
        assert (
            analysis.provenance.rubric_version >= 1 and analysis.provenance.vocabulary_version >= 1
        )


class TestTieredExecution:
    def test_the_face_model_only_sees_frames_where_a_person_was_found(self, venv: VideoEnv) -> None:
        faces, detector = FakeFaces(), FakeDetector(people_until=45)  # nobody in the second shot
        run(venv, clip(venv), profile(), engines(entities=detector, faces=faces))

        seen = faces.calls[0]
        detected = detector.calls[0]
        assert seen and set(seen) < set(detected)
        assert all(f < 45 for f in seen)

    def test_without_the_detector_the_face_model_looks_at_every_planned_frame(
        self, venv: VideoEnv
    ) -> None:
        faces = FakeFaces()
        use = engines(faces=faces, entities=FakeDetector(why_unavailable="no detector here"))

        result = run(venv, clip(venv), profile(), use)

        assert states(result)[AnalyzerId.ENTITIES] is AnalyzerState.NOT_AVAILABLE
        assert faces.calls and len(faces.calls[0]) > 3


class TestUnavailabilityCascades:
    def broken(self) -> dict[AnalyzerId, SignalAnalyzer]:
        return engines(entities=FakeDetector(why_unavailable="the detector weights are missing"))

    def test_a_missing_model_disables_that_analyzer_and_what_needs_it_and_nothing_else(
        self, venv: VideoEnv
    ) -> None:
        result = run(venv, clip(venv), profile(), self.broken())

        reports = {r.analyzer: r for r in result.analysis.analyzers}
        assert reports[AnalyzerId.ENTITIES].state is AnalyzerState.NOT_AVAILABLE
        assert "weights are missing" in reports[AnalyzerId.ENTITIES].reason
        assert reports[AnalyzerId.APPEARANCE].state is AnalyzerState.NOT_AVAILABLE
        assert "depends on entities" in reports[AnalyzerId.APPEARANCE].reason
        for fine in (AnalyzerId.SHOTS, AnalyzerId.QUALITY, AnalyzerId.FACES, AnalyzerId.EMBEDDINGS):
            assert reports[fine].state is AnalyzerState.OK, fine
        assert result.analysis.tracks == ()
        assert result.analysis.shots[0].entities.state is AnalyzerState.NOT_ANALYZED
        assert result.analysis.shots[0].faces.ok  # not lost

    def test_the_unavailable_result_is_reused_while_nothing_changes(self, venv: VideoEnv) -> None:
        asset = clip(venv)
        first = run(venv, asset, profile(), self.broken())
        decodes = venv.decodes()

        second = run(venv, asset, profile(), self.broken())

        assert not second.created and venv.decodes() == decodes
        assert second.analysis == first.analysis

    def test_when_the_model_arrives_only_what_was_missing_is_computed(self, venv: VideoEnv) -> None:
        asset = clip(venv)
        run(venv, asset, profile(), self.broken())

        healed = run(venv, asset, profile())

        assert healed.created
        assert cache(healed)[AnalyzerId.SHOTS] is CacheOutcome.REUSED
        assert cache(healed)[AnalyzerId.EMBEDDINGS] is CacheOutcome.REUSED
        assert cache(healed)[AnalyzerId.ENTITIES] is CacheOutcome.COMPUTED
        assert cache(healed)[AnalyzerId.APPEARANCE] is CacheOutcome.COMPUTED
        assert set(states(healed).values()) == {AnalyzerState.OK}

    def test_a_profile_cannot_run_without_the_shots_analyzer(self, venv: VideoEnv) -> None:
        from media_house.modules.video_intelligence.application.contracts import (
            VideoIntelligenceError,
        )

        use = engines()
        del use[AnalyzerId.SHOTS]

        result = venv.build(use).execute(
            AnalyzeVideoCommand(clip(venv), profile()), JobContext.detached()
        )

        assert not isinstance(result, Ok)
        assert isinstance(result.error, VideoIntelligenceError)


class TestInvalidationFollowsDependencies:
    def test_a_new_detector_version_recomputes_what_reads_its_detections_and_no_more(
        self, venv: VideoEnv
    ) -> None:
        asset = clip(venv)
        run(venv, asset, profile())

        second = run(venv, asset, profile(), engines(entities=FakeDetector(version="2")))

        assert cache(second)[AnalyzerId.ENTITIES] is CacheOutcome.COMPUTED
        assert cache(second)[AnalyzerId.FACES] is CacheOutcome.COMPUTED  # tiered by the detections
        assert cache(second)[AnalyzerId.APPEARANCE] is CacheOutcome.COMPUTED
        assert cache(second)[AnalyzerId.EMBEDDINGS] is CacheOutcome.REUSED
        assert cache(second)[AnalyzerId.SHOTS] is CacheOutcome.REUSED

    def test_a_changed_model_setting_recomputes_only_that_analyzer_family(
        self, venv: VideoEnv
    ) -> None:
        asset = clip(venv)
        base = profile()
        run(venv, asset, base)
        stricter = replace(base, measurement=replace(base.measurement, embed_every=2))

        second = run(venv, asset, stricter)

        assert cache(second)[AnalyzerId.EMBEDDINGS] is CacheOutcome.COMPUTED
        assert cache(second)[AnalyzerId.ENTITIES] is CacheOutcome.REUSED
        assert cache(second)[AnalyzerId.FACES] is CacheOutcome.REUSED

    def test_a_changed_scoring_threshold_rederives_without_decoding_or_running_a_model(
        self, venv: VideoEnv
    ) -> None:
        asset = clip(venv)
        base = profile()
        detector = FakeDetector()
        run(venv, asset, base, engines(entities=detector))
        decodes, calls = venv.decodes(), len(detector.calls)
        louder = replace(base, cinema=replace(base.cinema, close_up_face=0.2, medium_face=0.05))

        second = run(venv, asset, louder, engines(entities=detector))

        assert venv.decodes() == decodes and len(detector.calls) == calls
        assert set(cache(second).values()) == {CacheOutcome.REUSED}


class TestDeviceReport:
    def test_the_device_that_ran_the_models_is_recorded_and_never_changes_the_cache(
        self, venv: VideoEnv
    ) -> None:
        asset = clip(venv)
        gpu = engines(entities=FakeDetector(on_device="gpu"))

        first = run(venv, asset, profile(), gpu, RuntimeConfig(DeviceKind.GPU))
        second = run(venv, asset, profile(), gpu, RuntimeConfig(DeviceKind.CPU))

        assert (
            first.analysis.provenance.device_requested,
            first.analysis.provenance.device_used,
        ) == (
            "gpu",
            "gpu",
        )
        assert not second.created  # asking for the CPU instead is the same result

    def test_a_gpu_request_that_nothing_could_honour_is_reported(self, venv: VideoEnv) -> None:
        result = run(
            venv, clip(venv), profile(AnalyzerId.SHOTS), engines(), RuntimeConfig(DeviceKind.GPU)
        )

        assert result.analysis.provenance.device_used == "cpu"
        assert any("gpu requested" in w for w in result.analysis.warnings)
