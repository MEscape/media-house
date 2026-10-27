"""Detection over real media: FFmpeg writes the tags, the real inspection reads them, the real
library stores them and the real wiring proposes the claims. Nothing is faked on the path."""

from pathlib import Path

import pytest

from media_house.modules.asset_relationships.application.contracts import (
    DETECTOR_IDS,
    AnalyzeAssetCommand,
    DetectCommand,
    DetectStatus,
    Grade,
    RelationshipDetection,
    RelationshipQuery,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.media_inspection.application.contracts import (
    InspectMediaCommand,
    MediaInspector,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from tests.integration.asset_relationships.conftest import Env
from tests.support.media_factories import ffmpeg

pytestmark = pytest.mark.integration
CTX = JobContext.detached()


def clip(path: Path, *, created: str, seconds: int = 6, **tags: str) -> Path:
    args = ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=25"]
    for key, value in {"creation_time": created, **tags}.items():
        args += ["-metadata", f"{key}={value}"]
    ffmpeg(*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))
    return path


def inspect(env: Env, asset_id: str) -> None:
    result = env.proxy.container.resolve(MediaInspector).execute(InspectMediaCommand(asset_id), CTX)
    assert isinstance(result, Ok), result


def test_real_tags_become_graded_proposed_claims_and_a_repeat_changes_nothing(
    env: Env, tmp_path: Path
) -> None:
    a = env.import_media(
        clip(
            tmp_path / "a.mkv",
            created="2026-03-04T05:00:00Z",
            scene="12A",
            take="1",
            device="Cam One",
        )
    )
    b = env.import_media(
        clip(
            tmp_path / "b.mkv",
            created="2026-03-04T05:00:02Z",
            scene="12a",
            take="2",
            device="Cam One",
        )
    )
    c = env.import_media(
        clip(
            tmp_path / "c.mkv",
            created="2026-03-04T09:00:00Z",
            scene="7",
            take="1",
            device="Cam Two",
        )
    )
    for asset in (a, b, c):
        inspect(env, asset)
    detection = env.proxy.container.resolve(RelationshipDetection)
    assert set(DETECTOR_IDS) == {"wall_clock_overlap", "production_tag_takes"}

    first = detection.detect(DetectCommand(frozenset(DETECTOR_IDS)), CTX)

    assert isinstance(first, Ok) and first.value.status is DetectStatus.COMPLETED
    page = env.catalog.relationships_of(a, RelationshipQuery(include_projected=False))
    assert isinstance(page, Ok)
    by_type = {r.type: r for r in page.value.items}
    assert set(by_type) == {
        RelationshipType.TEMPORALLY_OVERLAPS_WITH,
        RelationshipType.ALTERNATE_TAKE_OF,
    }
    take = by_type[RelationshipType.ALTERNATE_TAKE_OF]
    overlap = by_type[RelationshipType.TEMPORALLY_OVERLAPS_WITH]
    assert take.status is overlap.status is RelationshipStatus.PROPOSED
    assert take.grade is Grade.CORROBORATED  # same scene, different take, same device
    assert overlap.grade is Grade.INDICATED  # the same named device on both clips
    assert {take.source_asset_id, take.target_asset_id} == {a, b}
    # the unrelated clip (other scene, other device, hours later) is linked to nothing
    other = env.catalog.relationships_of(c, RelationshipQuery(include_projected=False))
    assert isinstance(other, Ok) and other.value.items == ()

    again = detection.detect(DetectCommand(frozenset(DETECTOR_IDS)), CTX)
    single = detection.analyze(AnalyzeAssetCommand(b, frozenset(DETECTOR_IDS)), CTX)

    assert isinstance(again, Ok) and again.value.assets_visited == 0
    assert isinstance(single, Ok) and all(r.unchanged for r in single.value.results)


def test_an_asset_that_was_never_inspected_is_unavailable_and_nothing_is_probed(
    env: Env, tmp_path: Path
) -> None:
    a = env.import_media(clip(tmp_path / "x.mkv", created="2026-03-04T05:00:00Z"))
    detection = env.proxy.container.resolve(RelationshipDetection)

    result = detection.analyze(AnalyzeAssetCommand(a, frozenset(DETECTOR_IDS)), CTX)

    assert isinstance(result, Ok) and not result.value.inspection_available
