"""Detection: the rules, the walk, and how it behaves with human decisions (real SQLite store)."""

import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from media_house.modules.asset_relationships.application.detect_relationships import (
    AnalyzeAssetCommand,
    DetectCommand,
    DetectionSettings,
    DetectReason,
    DetectStatus,
)
from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.detection import (
    DETECTORS,
    DetectorVersions,
    InspectionFacts,
    Observed,
    ProductionTakes,
    WallClockOverlap,
)
from media_house.modules.asset_relationships.domain.detection_store import MarkOutcome
from media_house.modules.asset_relationships.domain.repository import RelationshipCriteria
from media_house.modules.asset_relationships.domain.values import (
    EvidenceKind,
    Grade,
    RelationshipId,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, OperationCancelledError
from tests.support.detection_fakes import START, Rig, inspection
from tests.support.relationship_fakes import original

OVERLAP = "wall_clock_overlap"
TAKES = "production_tag_takes"
CTX = JobContext.detached()


def facts(asset_id: str = "a", **changes: object) -> InspectionFacts:
    base = {
        "asset_id": asset_id,
        "inspection_id": f"insp-{asset_id}",
        "inspection_fingerprint": "fp",
        "duration_seconds": 60.0,
        "creation_time": START,
        "device": None,
        "reel_id": None,
        "scene": None,
        "take": None,
    }
    return InspectionFacts(**{**base, **changes})  # type: ignore[arg-type]


def observed(asset_id: str, detector: object, **changes: object) -> Observed:
    fact = detector.fact_of(facts(asset_id, **changes))  # type: ignore[attr-defined]
    assert fact is not None
    return Observed(asset_id, f"insp-{asset_id}:fp", f"insp-{asset_id}", fact)


# --- the rules -----------------------------------------------------------------------------
class TestFacts:
    def test_a_naive_timestamp_is_never_used(self) -> None:
        naive = START.replace(tzinfo=None)

        assert WallClockOverlap().fact_of(facts(creation_time=naive)) is None

    @pytest.mark.parametrize("seconds", [None, 0.0, -5.0])
    def test_no_usable_duration_means_no_span(self, seconds: float | None) -> None:
        assert WallClockOverlap().fact_of(facts(duration_seconds=seconds)) is None

    def test_the_span_is_exact_microseconds_in_utc(self) -> None:
        fact = WallClockOverlap().fact_of(facts(duration_seconds=1.5))

        assert fact is not None and fact.start_us is not None and fact.end_us is not None
        assert fact.end_us - fact.start_us == 1_500_000

    @pytest.mark.parametrize(("scene", "take"), [(None, "1"), ("A", None), ("  ", "1"), ("A", " ")])
    def test_tags_need_both_a_scene_and_a_take(self, scene: str | None, take: str | None) -> None:
        assert ProductionTakes().fact_of(facts(scene=scene, take=take)) is None

    def test_the_scene_is_grouped_case_insensitively_and_kept_as_written(self) -> None:
        fact = ProductionTakes().fact_of(facts(scene=" Scene 4B ", take="2"))

        assert fact is not None and fact.group_key == "scene 4b"
        assert fact.detail["scene"] == "Scene 4B"


class TestOverlapRule:
    detector = WallClockOverlap()

    def pair(self, offset_s: float, seconds: float = 60.0, **b: object):  # type: ignore[no-untyped-def]
        one = observed("a", self.detector, duration_seconds=seconds)
        other = observed(
            "b",
            self.detector,
            creation_time=START + timedelta(seconds=offset_s),
            duration_seconds=seconds,
            **b,
        )
        return self.detector.match(one, other)

    def test_back_to_back_clips_are_not_an_overlap(self) -> None:
        assert self.pair(60.0) is None  # touching
        assert self.pair(59.0) is None  # overlap equals the two margins: still a rounding
        assert self.pair(57.5) is not None

    def test_an_overlap_carries_where_it_lies_on_each_clip(self) -> None:
        detection = self.pair(30.0)

        assert detection is not None
        assert (detection.a_interval.start, detection.a_interval.end) == (30, 60)
        assert (detection.b_interval.start, detection.b_interval.end) == (0, 30)

    def test_the_offset_maps_time_in_the_first_asset_to_the_second(self) -> None:
        detection = self.pair(30.0)

        assert detection is not None and detection.offset is not None
        assert detection.offset.seconds == -30  # b started 30 s after a: a's t is b's t - 30...
        # ... checked against the intervals: a's second half is b's first half
        assert detection.a_interval.start + detection.offset.seconds == detection.b_interval.start

    def test_clocks_are_only_trusted_on_the_same_named_device(self) -> None:
        other = self.detector.fact_of(facts(device="Cam A"))
        assert other is not None
        same = self.pair(10.0, device="Cam A")
        mixed = self.pair(10.0, device="Cam B")

        # observed("a") has no device, so neither pair is "same_device" here
        assert same is not None and mixed is not None
        assert same.evidence[0].detail["clock_trust"] == "unverified"

        one = observed("a", self.detector, device="Cam A")
        two = observed(
            "b", self.detector, device="Cam A", creation_time=START + timedelta(seconds=10)
        )
        matched = self.detector.match(one, two)
        assert matched is not None and matched.evidence[0].detail["clock_trust"] == "same_device"

    def test_the_evidence_says_what_it_assumes(self) -> None:
        detection = self.pair(10.0)

        assert detection is not None
        detail = detection.evidence[0].detail
        assert detail["creation_time_semantics"] == "assumed_start"
        assert detail["inspection_a"] == "insp-a" and detail["inspection_b"] == "insp-b"
        assert detection.evidence[0].kind is EvidenceKind.WALL_CLOCK_OVERLAP


class TestTakeRule:
    detector = ProductionTakes()

    def match(self, take_b: str = "2", **b: object):  # type: ignore[no-untyped-def]
        one = observed("a", self.detector, scene="12", take="1", device="Cam A")
        other = observed(
            "b", self.detector, **{"scene": "12", "take": take_b, "device": "Cam A", **b}
        )
        return self.detector.match(one, other)

    def test_a_different_take_of_the_same_scene_on_the_same_device(self) -> None:
        detection = self.match()

        assert detection is not None
        assert detection.evidence[0].detail["same_device"] is True
        assert detection.relationship_type is RelationshipType.ALTERNATE_TAKE_OF

    def test_equal_takes_are_not_alternates(self) -> None:
        assert self.match(take_b=" 1 ") is None

    def test_a_generic_scene_label_alone_is_not_enough(self) -> None:
        assert self.match(device="Cam B") is None
        assert self.match(device=None) is None

    def test_the_same_reel_is_enough(self) -> None:
        one = observed("a", self.detector, scene="12", take="1", reel_id="R1")
        other = observed("b", self.detector, scene="12", take="3", reel_id="R1", device="Cam Z")

        detection = self.detector.match(one, other)

        assert detection is not None and detection.evidence[0].detail["same_reel"] is True


def test_detector_versions_know_registered_detectors_only() -> None:
    check = DetectorVersions()

    assert check.is_current(Basis(OVERLAP, "1")) is True
    assert check.is_current(Basis(OVERLAP, "0")) is False
    assert check.is_current(Basis("unknown-detector", "1")) is None
    assert check.is_current(Basis.manual()) is None
    assert set(DETECTORS) == {OVERLAP, TAKES}


# --- the job -------------------------------------------------------------------------------
def claims(rig: Rig, asset_id: str):  # type: ignore[no-untyped-def]
    return rig.repository.find(RelationshipCriteria(asset_id, limit=200))


def detect(rig: Rig, *detectors: str):  # type: ignore[no-untyped-def]
    result = rig.detection.detect(DetectCommand(frozenset(detectors or {OVERLAP, TAKES})), CTX)
    assert isinstance(result, Ok), result
    return result.value


class TestWalk:
    def test_nothing_runs_unless_a_detector_is_named(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)

        for names in (frozenset(), frozenset({"made-up"})):
            assert isinstance(rig.detection.detect(DetectCommand(names), CTX), Err)
            assert isinstance(rig.detection.analyze(AnalyzeAssetCommand("a", names), CTX), Err)
        assert rig.inspections.lookups == []

    def test_overlapping_recordings_become_a_proposed_claim_with_evidence(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        rig.add("a", minutes=0)
        rig.add("b", minutes=1, created=START + timedelta(seconds=30))

        outcome = detect(rig, OVERLAP)

        assert outcome.status is DetectStatus.COMPLETED and outcome.assets_visited == 2
        [claim] = claims(rig, "a")
        assert claim.type is RelationshipType.TEMPORALLY_OVERLAPS_WITH
        assert claim.status is RelationshipStatus.PROPOSED
        assert claim.origin is RelationshipOrigin.DETECTOR
        assert claim.grade is Grade.SUGGESTIVE  # different (unnamed) devices: clocks unverified
        assert claim.basis.detector == OVERLAP and set(claim.basis.inputs) == {"a", "b"}
        assert claim.offset is not None and claim.a_interval is not None

    def test_unrelated_recordings_produce_nothing_and_are_marked_as_looked_at(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        rig.add("a", minutes=0)
        rig.add("b", minutes=1, created=START + timedelta(hours=5))

        outcome = detect(rig, OVERLAP)

        assert outcome.proposed == 0 and claims(rig, "a") == []
        mark = rig.store.get_mark("a", OVERLAP)
        assert mark is not None and mark.outcome is MarkOutcome.NONE

    def test_a_missing_inspection_is_unavailable_never_none_and_never_probed(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        rig.add("a", inspect_it=False)

        outcome = detect(rig, OVERLAP)

        assert outcome.unavailable == 1 and outcome.proposed == 0
        mark = rig.store.get_mark("a", OVERLAP)
        assert mark is not None and mark.outcome is MarkOutcome.UNAVAILABLE

    def test_an_unavailable_asset_is_looked_at_again_when_its_inspection_appears(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        rig.add("a", minutes=0)
        rig.add("b", minutes=1, inspect_it=False)
        detect(rig, OVERLAP)
        assert claims(rig, "a") == []

        rig.inspections.results["b"] = inspection("b", created=START + timedelta(seconds=20))
        result = rig.detection.analyze(AnalyzeAssetCommand("b", frozenset({OVERLAP})), CTX)

        assert isinstance(result, Ok) and result.value.results[0].proposed == 1
        assert len(claims(rig, "a")) == 1

    def test_a_repeat_job_does_no_analysis_and_changes_nothing(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        rig.add("a", minutes=0)
        rig.add("b", minutes=1, created=START + timedelta(seconds=30))
        detect(rig, OVERLAP)
        before = [(c.id.value, c.updated_at, len(c.evidence)) for c in claims(rig, "a")]
        rig.inspections.lookups.clear()

        again = detect(rig, OVERLAP)

        assert again.status is DetectStatus.COMPLETED and again.proposed == 0
        assert again.analyzed == 0
        assert [(c.id.value, c.updated_at, len(c.evidence)) for c in claims(rig, "a")] == before

    def test_a_new_asset_is_found_from_the_feed_without_rewalking_the_library(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        rig.add("a", minutes=0)
        rig.add("b", minutes=1, created=START + timedelta(seconds=30))
        detect(rig, OVERLAP)
        rig.add("c", minutes=2, created=START + timedelta(seconds=40))
        rig.inspections.lookups.clear()

        outcome = detect(rig, OVERLAP)

        assert rig.inspections.lookups == ["c"]  # the feed visited only the new asset
        assert outcome.proposed == 2  # c overlaps both a and b

    def test_a_late_commit_the_feed_missed_is_found_by_the_full_sweep(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        rig.add("b", minutes=5, created=START + timedelta(seconds=30))
        detect(rig, OVERLAP)
        rig.add("a", minutes=0)  # inserted with an OLDER timestamp than the feed cursor
        assert detect(rig, OVERLAP).proposed == 0  # the feed does not look behind itself

        swept = rig.detection.detect(DetectCommand(frozenset({OVERLAP}), full=True), CTX)

        assert isinstance(swept, Ok) and swept.value.sweep_completed
        assert len(claims(rig, "a")) == 1

    def test_a_budget_stops_the_job_and_the_next_one_resumes(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path, settings=DetectionSettings(batch_assets=2, max_assets_per_job=2))
        for i in range(5):
            rig.add(f"x{i}", minutes=i, created=START + timedelta(seconds=10 * i))

        first = detect(rig, OVERLAP)
        seen = list(rig.inspections.lookups)
        while detect(rig, OVERLAP).status is DetectStatus.PARTIAL:
            pass

        assert first.status is DetectStatus.PARTIAL and first.reason is DetectReason.BUDGET
        assert first.assets_visited == 2
        assert sorted(set(rig.inspections.lookups)) == [f"x{i}" for i in range(5)]
        assert seen == ["x0", "x1"]

    def test_assets_sharing_one_creation_time_are_each_visited_once(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path, settings=DetectionSettings(batch_assets=3, max_assets_per_job=100))
        for i in range(7):
            rig.library.add(original(f"t{i}", created_at=START))
            rig.inspections.results[f"t{i}"] = inspection(
                f"t{i}", created=START + timedelta(hours=i * 3)
            )

        detect(rig, OVERLAP)

        assert sorted(rig.inspections.lookups) == [f"t{i}" for i in range(7)]

    def test_cancelling_records_the_interruption_and_raises(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        rig.add("a")
        ctx = JobContext.detached()
        ctx.cancellation.cancel()

        with pytest.raises(OperationCancelledError):
            rig.detection.detect(DetectCommand(frozenset({OVERLAP})), ctx)

        last = rig.detection.last_run()
        assert last is not None and last.status is DetectStatus.INTERRUPTED


class TestDecisions:
    def test_a_rejected_pair_is_not_revived_and_a_confirmed_one_is_not_changed(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        rig.add("a", minutes=0)
        rig.add("b", minutes=1, created=START + timedelta(seconds=30))
        detect(rig, OVERLAP)
        [claim] = claims(rig, "a")
        stored = rig.repository.get(claim.id)
        assert stored is not None
        stored.reject(now=rig.clock.now(), note="different shoots")
        rig.repository.update(claim.id, lambda _existing: stored)

        # the inspection of b changes, so b is analysed again and proposes the pair again
        rig.inspections.results["b"] = inspection(
            "b", created=START + timedelta(seconds=35), fingerprint="fp-2"
        )
        rig.detection.analyze(AnalyzeAssetCommand("b", frozenset({OVERLAP})), CTX)

        [after] = claims(rig, "a")
        assert after.status is RelationshipStatus.REJECTED  # never revived
        assert after.outdated_since is not None  # but a person can see the evidence moved

    def test_a_pair_that_no_longer_matches_goes_stale_when_its_inputs_changed(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        rig.add("a", minutes=0)
        rig.add("b", minutes=1, created=START + timedelta(seconds=30))
        detect(rig, OVERLAP)

        rig.inspections.results["b"] = inspection(
            "b", created=START + timedelta(hours=9), fingerprint="fp-2"
        )
        result = rig.detection.analyze(AnalyzeAssetCommand("b", frozenset({OVERLAP})), CTX)

        assert isinstance(result, Ok) and result.value.results[0].made_stale == 1
        [claim] = claims(rig, "a")
        assert claim.status is RelationshipStatus.STALE  # not deleted; a person can look

    def test_a_version_bump_invalidates_the_analysis_not_the_decisions(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        rig.add("a", minutes=0)
        rig.add("b", minutes=1, created=START + timedelta(seconds=30))
        detect(rig, OVERLAP)
        mark = rig.store.get_mark("a", OVERLAP)
        assert mark is not None
        rig.store.put_mark(
            type(mark)(mark.asset_id, mark.detector, "0", mark.input_identity, mark.outcome, 1),
            analyzed_at="2026-01-01T00:00:00+00:00",
        )
        rig.inspections.lookups.clear()

        swept = rig.detection.detect(DetectCommand(frozenset({OVERLAP}), full=True), CTX)

        assert isinstance(swept, Ok) and swept.value.analyzed == 1  # only the stale mark again
        refreshed = rig.store.get_mark("a", OVERLAP)
        assert refreshed is not None and refreshed.detector_version == "1"


class TestCapAndDeterminism:
    def test_fan_out_is_capped_in_a_fixed_order_and_said_so(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path, settings=DetectionSettings(max_candidates_per_asset=2))
        for i in range(5):
            rig.add(f"c{i}", minutes=i, created=START + timedelta(seconds=i))
        for i in range(4):
            rig.detection.analyze(AnalyzeAssetCommand(f"c{i}", frozenset({OVERLAP})), CTX)

        result = rig.detection.analyze(AnalyzeAssetCommand("c4", frozenset({OVERLAP})), CTX)

        assert isinstance(result, Ok)
        [one] = result.value.results
        assert one.outcome is MarkOutcome.CAPPED and one.proposed == 2 and one.capped >= 1

    def test_the_same_library_always_gives_the_same_claims(self, tmp_path: Path) -> None:
        def run(name: str) -> list[str]:
            rig = Rig(tmp_path / name)
            for i in range(4):
                rig.add(f"d{i}", minutes=i, created=START + timedelta(seconds=7 * i))
            detect(rig, OVERLAP)
            return sorted(
                RelationshipId.of(c.type, c.asset_a, c.asset_b).value
                for i in range(4)
                for c in claims(rig, f"d{i}")
            )

        (tmp_path / "x").mkdir()
        (tmp_path / "y").mkdir()
        assert run("x") == run("y")


def test_take_detector_proposes_with_device_agreement_only(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.add("a", scene="12", take="1", device="Cam A")
    rig.add("b", minutes=1, scene="12", take="2", device="Cam A")
    rig.add("c", minutes=2, scene="12", take="3", device="Cam B")

    detect(rig, TAKES)

    pairs = {(c.asset_a, c.asset_b) for c in claims(rig, "a") + claims(rig, "c")}
    assert pairs == {("a", "b")}
    [claim] = claims(rig, "a")
    assert claim.grade is Grade.CORROBORATED  # same scene, different take, same device


def test_the_first_complete_feed_pass_counts_as_the_first_sweep(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.add("a", minutes=0)
    rig.add("b", minutes=1, created=START + timedelta(seconds=30))

    first = detect(rig, OVERLAP)
    second = detect(rig, OVERLAP)

    assert first.sweep_completed and first.assets_visited == 2  # each asset once, not twice
    assert not second.sweep_completed and second.assets_visited == 0


def test_the_derived_tables_can_be_wiped_without_touching_a_single_claim(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.add("a", minutes=0)
    rig.add("b", minutes=1, created=START + timedelta(seconds=30))
    detect(rig, OVERLAP)
    [claim] = claims(rig, "a")
    stored = rig.repository.get(claim.id)
    assert stored is not None
    stored.confirm(now=rig.clock.now(), note="same event")
    rig.repository.update(claim.id, lambda _existing: stored)
    before = rig.repository.get(claim.id)

    with closing(sqlite3.connect(tmp_path / "relationships.db")) as conn, conn:
        conn.execute("DELETE FROM analysis_marks")
        conn.execute("DELETE FROM observed_facts")
    detect(rig, OVERLAP)
    rig.detection.detect(DetectCommand(frozenset({OVERLAP}), full=True), CTX)

    after = rig.repository.get(claim.id)
    assert after is not None and before is not None
    assert (after.status, after.grade, len(after.history)) == (
        RelationshipStatus.CONFIRMED,
        before.grade,
        len(before.history),
    )
    assert len(claims(rig, "a")) == 1
