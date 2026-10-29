"""The pool watches exactly the analyses that enrich footage, under the owners' real names."""

from dataclasses import replace

from media_house.modules.audio_intelligence.domain.analysis.config import ANALYSIS_OPERATION
from media_house.modules.audio_visual_alignment.domain.values import RESULT_OPERATION
from media_house.modules.broll_intelligence.application.pool import WATCHED_ANALYSES, ids_of
from media_house.modules.media_library.application.contracts import MediaAssetDto
from tests.support.asset_dto import make_asset


def test_the_watched_names_are_the_owners_names() -> None:
    assert {ANALYSIS_OPERATION, RESULT_OPERATION} == WATCHED_ANALYSES


def derived(asset_id: str, kind: str) -> MediaAssetDto:
    return replace(make_asset(asset_id), metadata={"processing_type": kind})


def test_a_new_sound_or_sync_analysis_changes_the_identity_but_a_result_of_ours_does_not() -> None:
    before = ids_of([derived("a", "audio_intelligence")])

    more_sound = ids_of(
        [derived("a", "audio_intelligence"), derived("b", "audio_visual_alignment")]
    )
    ours = ids_of([derived("a", "audio_intelligence"), derived("c", "broll_analysis")])
    plan = ids_of([derived("a", "audio_intelligence"), derived("d", "edit_plan")])

    assert more_sound != before
    assert ours == before == plan
