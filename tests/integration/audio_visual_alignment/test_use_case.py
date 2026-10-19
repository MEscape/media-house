"""Reuse, acquisition policy, cache identity and the stored document, on a real library."""

from dataclasses import replace

import pytest

from media_house.modules.audio_visual_alignment.application.align import (
    AlignAudioVisual,
    AlignCommand,
)
from media_house.modules.audio_visual_alignment.application.results import AlignmentResults
from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.errors import (
    IncompatibleInputs,
    InvalidAlignmentConfig,
    MissingPrerequisite,
)
from media_house.modules.audio_visual_alignment.domain.serialization import alignment_from_json
from media_house.modules.audio_visual_alignment.domain.values import (
    RESULT_OPERATION,
    Acquire,
    HowObtained,
    StageState,
)
from media_house.shared.errors import Err, NotFoundError, Ok, ValidationError
from tests.integration.audio_visual_alignment.conftest import (
    AlignEnv,
    make_offset_clip,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def clip(aenv: AlignEnv) -> str:
    asset_id = aenv.import_clip(make_offset_clip(aenv.root / "clip.mp4"))
    aenv.analyze_video(asset_id)
    return asset_id


def supplied(aenv: AlignEnv, clip: str, **changes: object) -> AlignCommand:
    return AlignCommand(
        clip,
        audio_timeline=aenv.audio_timeline(clip),
        use_content=False,
        **changes,  # type: ignore[arg-type]
    )


class TestAcquisition:
    def test_stored_mode_never_computes_and_names_what_is_missing(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        result = aenv.refused(AlignCommand(clip, use_content=False))

        assert isinstance(result.error, MissingPrerequisite)
        assert "audio" in result.error.user_message
        assert aenv.audio_analyzer.calls == 0 and aenv.video_analyzer.calls == 0

    def test_a_missing_video_analysis_is_also_refused_in_stored_mode(self, aenv: AlignEnv) -> None:
        asset_id = aenv.import_clip(make_offset_clip(aenv.root / "raw.mp4"))

        result = aenv.refused(
            AlignCommand(asset_id, audio_timeline=aenv.audio_timeline(asset_id), use_content=False)
        )

        assert isinstance(result.error, MissingPrerequisite)
        assert aenv.video_analyzer.calls == 0

    def test_request_mode_asks_the_owner_exactly_once_then_reuses(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        first = aenv.align(AlignCommand(clip, acquire=Acquire.REQUEST, use_content=False))
        second = aenv.align(AlignCommand(clip, acquire=Acquire.STORED, use_content=False))

        assert aenv.audio_analyzer.calls == 1
        assert first.created and not second.created
        assert {r.name: r.how for r in first.document.inputs}["audio_timeline"] is (
            HowObtained.REQUESTED
        )
        # a cached document says how its inputs reached the run that CREATED it
        assert second.document == first.document
        assert second.asset == first.asset  # how an input was obtained is not part of the identity

    def test_request_mode_computes_a_missing_video_analysis_once(self, aenv: AlignEnv) -> None:
        asset_id = aenv.import_clip(make_offset_clip(aenv.root / "fresh.mp4"))
        command = AlignCommand(
            asset_id,
            acquire=Acquire.REQUEST,
            audio_timeline=aenv.audio_timeline(asset_id),
            use_content=False,
        )

        aenv.align(command)
        aenv.align(command)

        assert aenv.video_analyzer.calls == 1

    def test_supplied_results_are_used_as_given_and_nothing_is_looked_up(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        result = aenv.align(supplied(aenv, clip))

        assert {r.name: r.how for r in result.document.inputs}["audio_timeline"] is (
            HowObtained.SUPPLIED
        )
        assert aenv.audio_analyzer.calls == 0

    def test_the_inspection_is_requested_once_when_missing_and_then_reused(
        self, aenv: AlignEnv
    ) -> None:
        asset_id = aenv.import_clip(make_offset_clip(aenv.root / "uninspected.mp4"))
        aenv.analyze_video(asset_id)  # video analysis resolves the inspection itself
        first = aenv.align(supplied(aenv, asset_id))
        second = aenv.align(supplied(aenv, asset_id))

        records = {r.name: r.how for r in first.document.inputs}
        assert records["inspection"] in (HowObtained.REUSED, HowObtained.REQUESTED)
        assert not second.created

    def test_content_that_is_absent_is_reported_as_such_not_invented(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        result = aenv.align(replace(supplied(aenv, clip), use_content=True))

        doc = result.document
        assert {r.name: r.how for r in doc.inputs}["content_analysis"] is HowObtained.NOT_AVAILABLE
        assert next(s for s in doc.stages if s.stage == "text").state is StageState.NOT_AVAILABLE
        assert aenv.content_analyzer.calls == 0


class TestCacheIdentity:
    def test_an_identical_run_computes_nothing_and_returns_the_same_document(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        first = aenv.align(supplied(aenv, clip))
        second = aenv.align(supplied(aenv, clip))

        assert first.created and not second.created
        assert second.asset == first.asset
        assert second.document == first.document

    def test_a_different_profile_value_misses_and_the_old_result_survives(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        first = aenv.align(supplied(aenv, clip))
        tuned = aenv.align(
            supplied(aenv, clip, profile=replace(AlignmentProfile(), speaking_spread=0.05))
        )

        assert tuned.created and tuned.asset.id != first.asset.id
        assert not aenv.align(supplied(aenv, clip)).created

    def test_a_different_audio_analysis_misses(self, aenv: AlignEnv, clip: str) -> None:
        first = aenv.align(supplied(aenv, clip))
        other = aenv.align(
            AlignCommand(
                clip,
                audio_timeline=aenv.audio_timeline(clip, offset=0.25),
                use_content=False,
            )
        )

        assert other.created and other.asset.id != first.asset.id

    def test_a_different_clock_input_misses(self, aenv: AlignEnv, clip: str) -> None:
        first = aenv.align(supplied(aenv, clip))
        shifted = aenv.align(supplied(aenv, clip, audio_shift_seconds=1.0))

        assert shifted.created and shifted.asset.id != first.asset.id

    def test_the_stored_document_is_a_derived_asset_the_catalog_finds(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        result = aenv.align(supplied(aenv, clip))

        derived = [
            a
            for a in aenv.library.list_derived(clip)
            if a.metadata.get("processing_type") == RESULT_OPERATION
        ]
        assert [a.id for a in derived] == [result.asset.id]
        found = AlignmentResults(aenv.library).latest(clip)
        assert isinstance(found, Ok) and found.value is not None
        assert found.value[0] == result.document
        path = aenv.library.local_path(result.asset.id)
        assert isinstance(path, Ok)
        assert alignment_from_json(path.value.read_text(encoding="utf-8")) == result.document

    def test_a_damaged_stored_document_is_recomputed_not_trusted(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        first = aenv.align(supplied(aenv, clip))
        path = aenv.library.local_path(first.asset.id)
        assert isinstance(path, Ok)
        path.value.write_text("{not json", encoding="utf-8")

        again = aenv.align(supplied(aenv, clip))

        assert again.created and again.document == first.document

    def test_the_original_asset_and_the_upstream_results_are_untouched(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        before = aenv.asset(clip)
        derived_before = {a.id for a in aenv.library.list_derived(clip)}

        result = aenv.align(supplied(aenv, clip))

        assert aenv.asset(clip).checksum == before.checksum
        assert derived_before <= {a.id for a in aenv.library.list_derived(clip)}
        assert result.asset.id not in derived_before


class TestRefusals:
    def test_an_unknown_asset(self, aenv: AlignEnv) -> None:
        result = aenv.refused(AlignCommand("nope", use_content=False))

        assert isinstance(result.error, NotFoundError)

    def test_an_audio_file_is_not_a_video(self, aenv: AlignEnv) -> None:
        from tests.support.media_factories import make_wav

        wav = aenv.import_clip(make_wav(aenv.root / "talk.wav"))

        result = aenv.refused(AlignCommand(wav, use_content=False))

        assert isinstance(result.error, ValidationError)

    def test_a_non_finite_shift_is_refused_before_any_work(self, aenv: AlignEnv, clip: str) -> None:
        result = aenv.refused(supplied(aenv, clip, audio_shift_seconds=float("nan")))

        assert isinstance(result.error, InvalidAlignmentConfig)

    def test_an_unknown_profile_name(self, aenv: AlignEnv, clip: str) -> None:
        result = aenv.refused(supplied(aenv, clip, profile="turbo"))

        assert isinstance(result.error, InvalidAlignmentConfig)

    def test_an_audio_analysis_of_another_asset_is_incompatible(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        other = aenv.import_clip(make_offset_clip(aenv.root / "other.mp4", audio_start=0.25))

        result = aenv.refused(
            AlignCommand(clip, audio_timeline=aenv.audio_timeline(other), use_content=False)
        )

        assert isinstance(result.error, IncompatibleInputs)

    def test_a_separate_audio_asset_is_allowed_and_the_assumption_is_stated(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        other = aenv.import_clip(make_offset_clip(aenv.root / "dual.mp4", audio_start=0.25))

        result = aenv.align(
            AlignCommand(
                clip,
                audio_asset_id=other,
                audio_timeline=aenv.audio_timeline(other),
                use_content=False,
            )
        )

        assert any("separate asset" in w for w in result.document.warnings)

    def test_cancellation_stops_the_run(self, aenv: AlignEnv, clip: str) -> None:
        from media_house.shared.concurrency import CancellationToken, JobContext
        from media_house.shared.errors import OperationCancelledError

        token = CancellationToken()
        token.cancel()
        ctx = replace(JobContext.detached(), cancellation=token)

        with pytest.raises(OperationCancelledError):
            aenv.aligner().execute(supplied(aenv, clip), ctx)


def test_err_results_carry_actionable_user_messages(aenv: AlignEnv, clip: str) -> None:
    result = aenv.refused(AlignCommand(clip, use_content=False))

    assert isinstance(result, Err)
    assert "Acquire.REQUEST" in result.error.user_message


class TestFailedEmbeddingsAreNeverCachedAsTheEmbeddingResult:
    """A degraded result must not be served as the complete one after the engine recovers."""

    class Embedder:
        def __init__(self, fail: bool) -> None:
            self.fail = fail
            self.calls = 0

        def identity(self) -> str:
            return "fake/1/model"

        def unavailable(self) -> str | None:
            return None

        def embed(self, texts: list[str], cancellation: object) -> list[list[float]]:
            from media_house.shared.errors import ExternalSystemError

            self.calls += 1
            if self.fail:
                raise ExternalSystemError("boom", user_message="the model crashed")
            return [[1.0, 0.0] if "battery" in t else [0.0, 1.0] for t in texts]

    class Stub:
        """Hands over fixed facts, so the embedding step has shots with text to compare."""

        def __init__(self, resolved: object) -> None:
            self._resolved = resolved

        def resolve(self, request: object, ctx: object) -> Ok[object]:
            return Ok(self._resolved)

    def use_case(self, aenv: AlignEnv, embedder: object | None) -> AlignAudioVisual:
        from media_house.modules.audio_visual_alignment.application.resolver import Resolved
        from tests.support.avl_facts import audio, content, shot, video

        shots = [
            shot(f"s{i}", i * 10, i * 10 + 10, objects=("battery" if i == 2 else "chair",))
            for i in range(6)
        ]
        resolved = Resolved(
            audio=audio([], duration=60.0),
            video=video(shots),
            content=content([("p1", 20.0, 30.0, "the battery pack")]),
            clock=None,
            records=(),
            identities=(("video", "v"), ("audio", "a"), ("content", "c"), ("inspection", None)),
            warnings=(),
        )
        return AlignAudioVisual(
            aenv.library,
            self.Stub(resolved),  # type: ignore[arg-type]
            aenv.paths,
            aenv.clock,
            embedder,  # type: ignore[arg-type]
        )

    def test_the_words_only_fallback_is_keyed_as_words_only(
        self, aenv: AlignEnv, clip: str
    ) -> None:
        failing = self.Embedder(fail=True)
        degraded = aenv.align(AlignCommand(clip), self.use_case(aenv, failing))

        assert degraded.created and failing.calls == 1
        assert any("words only" in w for w in degraded.document.warnings)
        assert degraded.document.provenance.engines == {}

        # the engine recovers: the degraded document must NOT be served as the embedding result
        working = self.Embedder(fail=False)
        complete = aenv.align(AlignCommand(clip), self.use_case(aenv, working))
        assert complete.created and working.calls == 1
        assert complete.asset.id != degraded.asset.id
        assert complete.document.provenance.engines == {"text_embedder": "fake/1/model"}
        assert any(m.similarity is not None for m in complete.document.text_matches)

        # a run without any embedder IS the words-only result and finds the stored one
        plain = aenv.align(AlignCommand(clip), self.use_case(aenv, None))
        assert not plain.created and plain.asset.id == degraded.asset.id
