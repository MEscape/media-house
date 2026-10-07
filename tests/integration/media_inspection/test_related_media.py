"""Relationships between real assets: recorded facts and uncertain candidates."""

from pathlib import Path

import pytest

from media_house.modules.media_inspection.application.related_media import FindRelatedMedia
from media_house.modules.media_inspection.domain.relations import RelationCandidate, RelationKind
from media_house.modules.media_inspection.domain.values import Certainty
from media_house.shared.errors import Err, Ok
from tests.integration.media_inspection.conftest import Env
from tests.support.media_factories import ffmpeg

pytestmark = pytest.mark.integration


def related(env: Env, asset_id: str) -> dict[str, RelationCandidate]:
    result = FindRelatedMedia(env.library).execute(asset_id)
    assert isinstance(result, Ok), result
    return {c.asset_id: c for c in result.value.candidates}


def make(env: Env, name: str, *args: str) -> Path:
    path = env.media_dir / name
    ffmpeg(*args, str(path))
    return path


def test_a_re_encode_of_the_same_take_is_a_possible_duplicate_not_a_fact(env: Env) -> None:
    original = env.import_media(env.samples.cfr_25())
    copy_path = make(
        env,
        "copy.mp4",
        "-i",
        str(env.samples.cfr_25()),
        "-c:v",
        "mpeg4",
        "-q:v",
        "9",
        "-c:a",
        "aac",
    )
    copy = env.import_media(copy_path)

    candidate = related(env, original)[copy]

    assert candidate.kind is RelationKind.POSSIBLE_DUPLICATE
    assert candidate.certainty is Certainty.POSSIBLE
    assert 0.5 < candidate.confidence <= 0.9


def test_a_smaller_copy_of_the_same_length_is_a_possible_proxy(env: Env) -> None:
    original = env.import_media(env.samples.cfr_25())
    proxy_path = make(
        env, "proxy.mp4", "-i", str(env.samples.cfr_25()), "-vf", "scale=80:60", "-c:v", "mpeg4",
        "-c:a", "aac",
    )  # fmt: skip
    proxy = env.import_media(proxy_path)

    candidate = related(env, original)[proxy]

    assert candidate.kind is RelationKind.POSSIBLE_PROXY
    assert candidate.evidence["other_is"] == "lower_resolution"
    assert related(env, proxy)[original].evidence["other_is"] == "higher_resolution"


def test_a_derived_asset_is_recorded_as_a_fact_in_both_directions(env: Env) -> None:
    source = env.import_media(env.samples.cfr())
    registered = env.library.register_derived(
        source, env.samples.audio_only(), operation="test_extraction", config={"k": 1}
    )
    assert isinstance(registered, Ok)
    derived = registered.value.asset.id

    forward = related(env, source)[derived]
    backward = related(env, derived)[source]

    assert (forward.kind, forward.confidence, forward.certainty) == (
        RelationKind.DERIVATIVE,
        1.0,
        Certainty.MEASURED,
    )
    assert backward.kind is RelationKind.SOURCE


def test_unrelated_media_is_not_related(env: Env) -> None:
    subject = env.import_media(env.samples.cfr_25())
    env.import_media(env.samples.hole())  # 4 s: a different length
    env.import_media(env.samples.audio_only())  # the subject has audio too: not a companion

    assert related(env, subject) == {}


def test_an_unknown_asset_is_an_error_result(env: Env) -> None:
    assert isinstance(FindRelatedMedia(env.library).execute("missing"), Err)


def test_the_inspection_documents_themselves_are_not_candidates(env: Env) -> None:
    source = env.import_media(env.samples.cfr())
    inspection = env.inspect(source)

    assert inspection.asset.id not in related(env, source)
