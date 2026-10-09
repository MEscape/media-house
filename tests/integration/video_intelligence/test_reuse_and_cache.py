"""Nothing is done twice: upstream facts are reused, each analyzer is cached on its own."""

import dataclasses
from pathlib import Path

import pytest

from media_house.modules.video_intelligence.application.analyze_video import AnalyzeVideoCommand
from media_house.modules.video_intelligence.application.contracts import (
    ANALYZERS,
    AnalyzerId,
    CacheOutcome,
    InputSource,
    analysis_from_json,
)
from media_house.modules.video_intelligence.application.ports import MeasureRequest
from media_house.modules.video_intelligence.infrastructure.numpy_signals import (
    NumpyQualityAnalyzer,
    NumpyShotAnalyzer,
)
from media_house.modules.video_intelligence.module import classical_analyzers
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import ExternalSystemError, Ok
from tests.integration.video_intelligence.conftest import VideoEnv
from tests.support import vi_footage as vf

pytestmark = pytest.mark.integration


def clip(venv: VideoEnv, name: str = "clip", seed: int = 1) -> str:
    frame, total, _ = vf.edit(
        [vf.Segment(vf.still(seed), 45), vf.Segment(vf.pan(seed + 1, 2.0), 45)]
    )
    return venv.import_clip(vf.write(venv.media_dir / f"{name}.mp4", frame, total))


def sources(result) -> dict[str, InputSource]:  # type: ignore[no-untyped-def]
    return {u.name: u.source for u in result.analysis.inputs_used}


def cache_states(result) -> dict[AnalyzerId, CacheOutcome]:  # type: ignore[no-untyped-def]
    return {r.analyzer: r.cache for r in result.analysis.analyzers}


class TestUpstreamFacts:
    def test_a_stored_inspection_is_reused_and_nothing_is_probed(self, venv: VideoEnv) -> None:
        asset = clip(venv)
        venv.inspect(asset)
        probes = venv.probes_of(asset)

        result = venv.analyze(asset)

        assert venv.probes_of(asset) == probes  # zero additional probing of the video
        assert sources(result)["media_inspection"] is InputSource.REUSED

    def test_without_an_inspection_the_owner_is_asked_and_stores_it(self, venv: VideoEnv) -> None:
        asset = clip(venv)
        before = venv.probes_of(asset)  # the library's own look at the file on import

        result = venv.analyze(asset)

        assert sources(result)["media_inspection"] is InputSource.REQUESTED
        assert venv.probes_of(asset) > before  # the OWNER probed it
        assert venv.inspector.find(asset) is not None  # ... and kept the result for everyone

    def test_the_second_asker_reuses_what_the_first_caused(self, venv: VideoEnv) -> None:
        first = clip(venv)
        venv.analyze(first)
        probes = venv.probes_of(first)

        # a different consumer (a fresh service over the same library) finds the stored inspection
        again = venv.analyze(first, "triage", use_case=venv.build())

        assert venv.probes_of(first) == probes
        assert sources(again)["media_inspection"] is InputSource.REUSED

    def test_this_module_contains_no_probing_code(self) -> None:
        root = Path(__file__).resolve().parents[3] / "src/media_house/modules/video_intelligence"
        offending = [
            path.name
            for path in root.rglob("*.py")
            if any(
                marker in path.read_text(encoding="utf-8")
                for marker in ("ffprobe", "-show_streams", "-show_format", "import subprocess")
            )
        ]

        assert offending == []


class TestNoRecomputation:
    def test_an_identical_second_run_computes_nothing_and_returns_an_equal_result(
        self, venv: VideoEnv
    ) -> None:
        asset = clip(venv)
        first = venv.analyze(asset)
        calls = len(venv.runner.calls)

        second = venv.analyze(asset, use_case=venv.build())  # also after a restart

        assert first.created and not second.created
        new = venv.runner.calls[calls:]
        assert all(args == ("-version",) for _tool, args in new)  # only tool-version look-ups
        assert second.analysis == first.analysis
        assert second.asset.id == first.asset.id

    def test_the_analyzers_share_one_decode(self, venv: VideoEnv) -> None:
        asset = clip(venv)

        venv.analyze(asset, "standard")

        assert venv.decodes() == 1  # shots, quality and motion all read the same decode

    def test_every_analyzer_is_computed_once_on_a_first_run(self, venv: VideoEnv) -> None:
        result = venv.analyze(clip(venv))

        assert set(cache_states(result).values()) == {CacheOutcome.COMPUTED}
        assert set(result.signal_assets) == {
            AnalyzerId.SHOTS,
            AnalyzerId.QUALITY,
            AnalyzerId.MOTION,
        }

    def test_the_result_and_its_signals_are_derived_assets_of_the_video(
        self, venv: VideoEnv
    ) -> None:
        asset = clip(venv)

        result = venv.analyze(asset)

        derived = {a.id for a in venv.library.list_derived(asset)}
        assert result.asset.id in derived
        assert {a.id for a in result.signal_assets.values()} <= derived
        assert result.asset.derivation is not None
        assert result.asset.derivation.source_asset_id == asset
        assert (
            analysis_from_json(
                venv.library.local_path(result.asset.id).value.read_text(encoding="utf-8")  # type: ignore[union-attr]
            )
            == result.analysis
        )


class TestProfilesShareWork:
    def test_upgrading_from_triage_computes_only_the_missing_analyzer(self, venv: VideoEnv) -> None:
        asset = clip(venv)
        triage = venv.analyze(asset, "triage")
        decodes = venv.decodes()

        fast = venv.analyze(asset, "fast")

        states = cache_states(fast)
        assert states[AnalyzerId.SHOTS] is CacheOutcome.REUSED
        assert states[AnalyzerId.QUALITY] is CacheOutcome.REUSED
        assert states[AnalyzerId.MOTION] is CacheOutcome.COMPUTED
        assert venv.decodes() == decodes + 1
        decode = [
            a for tool, a in venv.runner.calls if tool == "ffmpeg" and "-filter_complex" in a
        ][-1]
        assert "[samples]" in " ".join(decode) and "[dense]" not in " ".join(
            decode
        )  # no full re-decode
        assert fast.signal_assets[AnalyzerId.SHOTS].id == triage.signal_assets[AnalyzerId.SHOTS].id

    def test_the_triage_result_does_not_pretend_to_know_camera_motion(self, venv: VideoEnv) -> None:
        result = venv.analyze(clip(venv), "triage")

        assert {r.analyzer for r in result.analysis.analyzers} == {
            AnalyzerId.SHOTS,
            AnalyzerId.QUALITY,
        }
        assert all(s.camera.reasons == ("motion_analyzer_not_run",) for s in result.analysis.shots)

    def test_a_denser_profile_reuses_the_shot_signals_but_measures_the_samples_again(
        self, venv: VideoEnv
    ) -> None:
        asset = clip(venv)
        venv.analyze(asset, "fast")

        standard = venv.analyze(asset, "standard")

        states = cache_states(standard)
        assert states[AnalyzerId.SHOTS] is CacheOutcome.REUSED
        assert states[AnalyzerId.QUALITY] is CacheOutcome.COMPUTED
        assert states[AnalyzerId.MOTION] is CacheOutcome.COMPUTED


class TestInvalidation:
    def test_a_changed_threshold_rederives_from_stored_signals_without_decoding(
        self, venv: VideoEnv
    ) -> None:
        asset = clip(venv)
        base = venv.profile("standard")
        first = venv.analyze(asset, base)
        decodes = venv.decodes()
        stricter = dataclasses.replace(
            base, shots=dataclasses.replace(base.shots, cut_diff=0.9, dissolve_diff=0.9)
        )

        second = venv.analyze(asset, stricter)

        assert venv.decodes() == decodes
        assert set(cache_states(second).values()) == {CacheOutcome.REUSED}
        assert len(first.analysis.shots) == 2 and len(second.analysis.shots) == 1
        assert second.asset.id != first.asset.id  # a different result, found by its own key

    def test_a_new_analyzer_version_recomputes_that_analyzer_and_its_dependents_only(
        self, venv: VideoEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asset = clip(venv)
        venv.analyze(asset)
        quality = ANALYZERS[AnalyzerId.QUALITY]
        monkeypatch.setitem(ANALYZERS, AnalyzerId.QUALITY, dataclasses.replace(quality, version=2))

        second = venv.analyze(asset, use_case=venv.build())

        states = cache_states(second)
        assert states[AnalyzerId.QUALITY] is CacheOutcome.COMPUTED
        assert states[AnalyzerId.SHOTS] is CacheOutcome.REUSED
        assert states[AnalyzerId.MOTION] is CacheOutcome.REUSED
        assert second.analysis.provenance.analyzer_versions["quality"] == 2

    def test_a_new_version_of_a_dependency_recomputes_what_depends_on_it(
        self, venv: VideoEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asset = clip(venv)
        venv.analyze(asset)
        shots = ANALYZERS[AnalyzerId.SHOTS]
        monkeypatch.setitem(ANALYZERS, AnalyzerId.SHOTS, dataclasses.replace(shots, version=2))

        second = venv.analyze(asset, use_case=venv.build())

        assert set(cache_states(second).values()) == {CacheOutcome.COMPUTED}

    def test_a_new_tool_version_invalidates_the_signals(self, venv: VideoEnv) -> None:
        asset = clip(venv)
        venv.analyze(asset)

        class Newer(NumpyQualityAnalyzer):
            def identity(self) -> dict[str, str]:
                return {**super().identity(), "numpy": "99.0"}

        engines = classical_analyzers()
        engines[AnalyzerId.QUALITY] = Newer()
        second = venv.analyze(asset, use_case=venv.build(engines))

        assert cache_states(second)[AnalyzerId.QUALITY] is CacheOutcome.COMPUTED
        assert second.analysis.provenance.engines["quality.numpy"] == "99.0"
        assert second.analysis.provenance.engines["shots.numpy"] != "99.0"

    def test_a_different_asset_version_is_a_different_cache_entry(self, venv: VideoEnv) -> None:
        first, second = clip(venv, "a", 1), clip(venv, "b", 7)

        one, two = venv.analyze(first), venv.analyze(second)

        assert one.asset.id != two.asset.id
        assert one.analysis.asset_checksum != two.analysis.asset_checksum
        assert set(cache_states(two).values()) == {CacheOutcome.COMPUTED}

    def test_a_damaged_stored_document_is_recomputed_and_replaced(self, venv: VideoEnv) -> None:
        asset = clip(venv)
        first = venv.analyze(asset)
        path = venv.library.local_path(first.signal_assets[AnalyzerId.QUALITY].id).value  # type: ignore[union-attr]
        path.write_text("{ not json", encoding="utf-8")
        for stored in (first.asset,):
            venv.library.local_path(stored.id).value.write_text("{ not json", encoding="utf-8")  # type: ignore[union-attr]

        second = venv.analyze(asset, use_case=venv.build())

        assert cache_states(second)[AnalyzerId.QUALITY] is CacheOutcome.COMPUTED
        assert cache_states(second)[AnalyzerId.SHOTS] is CacheOutcome.REUSED
        assert second.analysis.shots == first.analysis.shots
        assert venv.analyze(asset, use_case=venv.build()).created is False  # healthy again


class TestDeterminism:
    def test_two_independent_runs_agree_including_ids(
        self, venv: VideoEnv, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        from tests.integration.video_intelligence.conftest import build_video_env

        other = build_video_env(tmp_path_factory.mktemp("second"))
        one = venv.analyze(clip(venv)).analysis
        two = other.analyze(clip(other)).analysis

        assert [s.shot_id for s in one.shots] == [s.shot_id for s in two.shots]
        assert one.shots == two.shots
        assert one.curves == two.curves
        assert one.provenance == two.provenance
        assert one.asset_checksum == two.asset_checksum


class TestFailurePaths:
    def test_one_failing_analyzer_leaves_the_rest_of_the_result_intact(
        self, venv: VideoEnv
    ) -> None:
        class Failing(NumpyQualityAnalyzer):
            def measure(self, video, request: MeasureRequest, cancellation):  # type: ignore[no-untyped-def]
                raise ExternalSystemError("simulated engine failure")

        engines = classical_analyzers()
        engines[AnalyzerId.QUALITY] = Failing()
        asset = clip(venv)

        result = venv.analyze(asset, use_case=venv.build(engines))

        reports = {r.analyzer: r for r in result.analysis.analyzers}
        assert reports[AnalyzerId.QUALITY].state.value == "failed"
        assert "simulated engine failure" in reports[AnalyzerId.QUALITY].reason
        assert reports[AnalyzerId.SHOTS].state.value == "ok"
        assert reports[AnalyzerId.MOTION].state.value == "ok"
        assert len(result.analysis.shots) == 2
        assert all(s.camera.ok for s in result.analysis.shots)
        assert all(not s.quality.ok and s.quality.reasons for s in result.analysis.shots)
        assert AnalyzerId.QUALITY not in result.signal_assets

    def test_the_failed_analyzer_is_retried_next_time_while_the_others_are_reused(
        self, venv: VideoEnv
    ) -> None:
        class Failing(NumpyQualityAnalyzer):
            def measure(self, video, request, cancellation):  # type: ignore[no-untyped-def]
                raise ExternalSystemError("simulated engine failure")

        engines = classical_analyzers()
        engines[AnalyzerId.QUALITY] = Failing()
        asset = clip(venv)
        venv.analyze(asset, use_case=venv.build(engines))
        decodes = venv.decodes()

        healed = venv.analyze(asset, use_case=venv.build())

        states = cache_states(healed)
        assert states[AnalyzerId.QUALITY] is CacheOutcome.COMPUTED
        assert states[AnalyzerId.SHOTS] is CacheOutcome.REUSED
        assert states[AnalyzerId.MOTION] is CacheOutcome.REUSED
        assert venv.decodes() == decodes + 1

    def test_a_failing_shot_analyzer_fails_the_run_because_everything_needs_it(
        self, venv: VideoEnv
    ) -> None:
        class Failing(NumpyShotAnalyzer):
            def measure(self, video, request, cancellation):  # type: ignore[no-untyped-def]
                raise ExternalSystemError("cannot measure frame changes")

        engines = classical_analyzers()
        engines[AnalyzerId.SHOTS] = Failing()

        with pytest.raises(ExternalSystemError):
            venv.build(engines).execute(AnalyzeVideoCommand(clip(venv)), JobContext.detached())

    def test_cancellation_stops_the_run_and_stores_no_result(self, venv: VideoEnv) -> None:
        from media_house.shared.concurrency import CancellationToken
        from media_house.shared.errors import OperationCancelledError

        token = CancellationToken()
        token.cancel()
        ctx = dataclasses.replace(JobContext.detached(), cancellation=token)
        asset = clip(venv)

        with pytest.raises(OperationCancelledError):
            venv.build().execute(AnalyzeVideoCommand(asset), ctx)

        assert [
            a for a in venv.library.list_derived(asset) if "video_intelligence" in str(a.metadata)
        ] == []

    def test_a_gpu_request_runs_on_the_cpu_and_says_so(self, venv: VideoEnv) -> None:
        from media_house.modules.video_intelligence.application.contracts import (
            DeviceKind,
            RuntimeConfig,
        )

        asset = clip(venv)

        result = venv.build().execute(
            AnalyzeVideoCommand(asset, runtime=RuntimeConfig(DeviceKind.GPU)), JobContext.detached()
        )

        assert isinstance(result, Ok)
        provenance = result.value.analysis.provenance
        assert (provenance.device_requested, provenance.device_used) == ("gpu", "cpu")
        assert any("gpu" in w for w in result.value.analysis.warnings)


class TestConcurrency:
    def test_simultaneous_runs_share_engines_without_sharing_state(self, venv: VideoEnv) -> None:
        from concurrent.futures import ThreadPoolExecutor

        assets = [clip(venv, "a", 1), clip(venv, "b", 5)]
        engines = classical_analyzers()  # ONE set of engine instances serves both jobs

        with ThreadPoolExecutor(max_workers=2) as pool:
            runs = [
                pool.submit(venv.analyze, asset, "fast", venv.build(engines)) for asset in assets
            ]
            together = [run.result() for run in runs]
        alone = [
            venv.analyze(a, "fast", venv.build(engines)).analysis for a in assets
        ]  # served from the cache the concurrent runs filled

        assert [r.analysis for r in together] == alone
        assert together[0].asset.id != together[1].asset.id
        assert {r.analysis.asset_id for r in together} == set(assets)

    def test_two_simultaneous_runs_on_one_asset_converge_on_one_result(
        self, venv: VideoEnv
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor

        asset = clip(venv)

        with ThreadPoolExecutor(max_workers=2) as pool:
            runs = [pool.submit(venv.analyze, asset, "fast", venv.build()) for _ in range(2)]
            first, second = (run.result() for run in runs)

        assert first.analysis == second.analysis
        assert first.asset.id == second.asset.id  # one stored result, not two
