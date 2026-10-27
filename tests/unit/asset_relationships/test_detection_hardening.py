"""Hardening: timestamp edge cases, evidence freshness, failure recovery and races.

Everything here runs over one real SQLite file (claims, marks, facts and reconciliation), so the
behaviour is the stores' own, not a double's.
"""

import math
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from media_house.modules.asset_relationships.application.detect_relationships import (
    AnalyzeAssetCommand,
    DetectCommand,
    DetectReason,
    DetectStatus,
)
from media_house.modules.asset_relationships.application.reconciliation import ReconcileCommand
from media_house.modules.asset_relationships.domain.detection import (
    EARLIEST_PLAUSIBLE,
    MAX_PLAUSIBLE_SPAN_S,
    DetectorVersions,
    InspectionFacts,
    Observed,
    ProductionTakes,
    WallClockOverlap,
)
from media_house.modules.asset_relationships.domain.errors import IllegalTransition
from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.modules.asset_relationships.domain.repository import RelationshipCriteria
from media_house.modules.asset_relationships.domain.values import RelationshipStatus
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok, OperationCancelledError, PersistenceError
from tests.support.detection_fakes import START, Rig, inspection

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


def span(asset_id: str, offset_us: int = 0, seconds: float = 60.0) -> Observed:
    detector = WallClockOverlap()
    created = START + timedelta(microseconds=offset_us)
    fact = detector.fact_of(facts(asset_id, creation_time=created, duration_seconds=seconds))
    assert fact is not None
    return Observed(asset_id, f"insp-{asset_id}:fp", f"insp-{asset_id}", fact)


def claims(rig: Rig, asset_id: str):  # type: ignore[no-untyped-def]
    return rig.repository.find(RelationshipCriteria(asset_id, limit=200))


# --- timestamps ----------------------------------------------------------------------------
class TestTimestamps:
    detector = WallClockOverlap()

    def test_the_threshold_is_strictly_more_than_two_seconds(self) -> None:
        exactly = self.detector.match(span("a"), span("b", offset_us=58_000_000))  # 2.000000 s
        just_over = self.detector.match(span("a"), span("b", offset_us=57_999_999))  # 2.000001 s
        just_under = self.detector.match(span("a"), span("b", offset_us=58_000_001))

        assert exactly is None and just_under is None
        assert just_over is not None
        assert just_over.evidence[0].detail["overlap_us"] == 2_000_001  # the calculation, kept

    def test_the_same_instant_in_another_offset_gives_the_same_span(self) -> None:
        utc = self.detector.fact_of(facts(creation_time=START))
        plus_530 = self.detector.fact_of(
            facts(creation_time=START.astimezone(timezone(timedelta(hours=5, minutes=30))))
        )

        assert utc is not None and utc == plus_530

    def test_a_naive_time_is_never_read_as_utc_or_local(self) -> None:
        assert self.detector.fact_of(facts(creation_time=START.replace(tzinfo=None))) is None

    @pytest.mark.parametrize(
        "created",
        [
            datetime(1904, 1, 1, tzinfo=UTC),  # the QuickTime epoch an unset clock writes
            datetime(1970, 1, 1, tzinfo=UTC),  # the Unix epoch an unset clock writes
            datetime(1999, 12, 31, 23, 59, 59, tzinfo=UTC),
            datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5))),  # cannot be expressed in UTC
        ],
    )
    def test_an_unset_or_impossible_clock_is_not_a_time_of_recording(
        self, created: datetime
    ) -> None:
        assert self.detector.fact_of(facts(creation_time=created)) is None

    def test_the_earliest_plausible_time_itself_is_accepted(self) -> None:
        assert self.detector.fact_of(facts(creation_time=EARLIEST_PLAUSIBLE)) is not None

    @pytest.mark.parametrize(
        "seconds",
        [math.nan, math.inf, -math.inf, 0.0, -1.0, MAX_PLAUSIBLE_SPAN_S + 1, 1e12],
    )
    def test_an_unusable_or_implausible_duration_gives_no_span_and_never_raises(
        self, seconds: float
    ) -> None:
        assert self.detector.fact_of(facts(duration_seconds=seconds)) is None

    def test_the_longest_plausible_span_is_accepted(self) -> None:
        assert self.detector.fact_of(facts(duration_seconds=MAX_PLAUSIBLE_SPAN_S)) is not None

    def test_the_evidence_says_it_is_a_timestamp_overlap_and_nothing_more(self) -> None:
        detection = self.detector.match(span("a"), span("b", offset_us=10_000_000))

        assert detection is not None
        detail = detection.evidence[0].detail
        assert detail["evidence_scope"] == "timestamp_overlap_only"
        assert detail["margin_us"] == 1_000_000 and detail["clock_trust"] == "unverified"

    def test_clips_with_identical_unset_clocks_never_pair(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        for i, name in enumerate("abc"):
            rig.add(name, minutes=i, created=datetime(1970, 1, 1, tzinfo=UTC))

        outcome = rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)

        assert isinstance(outcome, Ok) and outcome.value.proposed == 0
        assert claims(rig, "a") == []


# --- production tags -----------------------------------------------------------------------
class TestTags:
    detector = ProductionTakes()

    def observed(self, asset_id: str, **changes: object) -> Observed:
        fact = self.detector.fact_of(facts(asset_id, **changes))
        assert fact is not None
        return Observed(asset_id, f"insp-{asset_id}:fp", f"insp-{asset_id}", fact)

    def test_the_same_reel_on_two_different_devices_is_contradictory_and_skipped(self) -> None:
        one = self.observed("a", scene="1", take="1", device="Cam A", reel_id="R1")
        other = self.observed("b", scene="1", take="2", device="Cam B", reel_id="R1")

        assert self.detector.match(one, other) is None

    def test_the_tags_that_justified_a_proposal_are_in_the_evidence(self) -> None:
        one = self.observed("a", scene=" Scene 4 ", take="1", device="Cam A")
        other = self.observed("b", scene="scene 4", take="2", device="Cam A")

        detection = self.detector.match(one, other)

        assert detection is not None
        detail = detection.evidence[0].detail
        assert (detail["scene_a"], detail["scene_b"]) == ("Scene 4", "scene 4")  # as written
        assert (detail["take_a"], detail["take_b"]) == ("1", "2")
        assert detail["same_device"] is True and detail["same_reel"] is False
        assert detail["inspection_a"] == "insp-a" and detail["inspection_b"] == "insp-b"

    def test_a_different_scene_is_never_a_take_even_when_similar(self) -> None:
        one = self.observed("a", scene="12A", take="1", device="Cam A")
        other = self.observed("b", scene="12B", take="2", device="Cam A")

        assert one.fact.group_key != other.fact.group_key  # the store never even offers it

    def test_device_names_are_compared_exactly_not_guessed_equal(self) -> None:
        one = self.observed("a", scene="1", take="1", device="Cam A")
        other = self.observed("b", scene="1", take="2", device="cam a")

        assert self.detector.match(one, other) is None


# --- freshness and lifecycle ---------------------------------------------------------------
class V2Overlap(WallClockOverlap):
    version = "2"


def two_overlapping(rig: Rig) -> None:
    rig.add("a", minutes=0)
    rig.add("b", minutes=1, created=START + timedelta(seconds=30))


class TestFreshness:
    def test_a_detector_version_bump_stales_proposals_then_redetection_revives_them(
        self, tmp_path: Path
    ) -> None:
        old = Rig(tmp_path)
        two_overlapping(old)
        old.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)
        [proposal] = claims(old, "a")
        assert proposal.basis.version == "1"

        check = DetectorVersions({OVERLAP: V2Overlap()})
        old.reconciler(check).reconcile(ReconcileCommand(force=True), CTX)
        [after_reconcile] = claims(old, "a")
        assert after_reconcile.status is RelationshipStatus.STALE

        new = Rig(tmp_path, detectors={OVERLAP: V2Overlap()}, share=old)
        for asset in ("a", "b"):
            new.detection.analyze(AnalyzeAssetCommand(asset, frozenset({OVERLAP})), CTX)

        [revived] = claims(new, "a")
        assert revived.status is RelationshipStatus.PROPOSED
        assert revived.basis.version == "2"
        assert {e.provider_version for e in revived.current_evidence} == {"2"}

    def test_a_version_bump_flags_a_decided_claim_but_never_changes_it(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        two_overlapping(rig)
        rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)
        [claim] = claims(rig, "a")
        decided = rig.repository.get(claim.id)
        assert decided is not None
        decided.confirm(now=rig.clock.now(), note="same event")
        rig.repository.update(claim.id, lambda _e: decided)

        rig.reconciler(DetectorVersions({OVERLAP: V2Overlap()})).reconcile(
            ReconcileCommand(force=True), CTX
        )

        [after] = claims(rig, "a")
        assert after.status is RelationshipStatus.CONFIRMED  # a person's decision stands
        assert after.outdated_since is not None  # and they can see the basis moved

    def test_an_unrelated_asset_changing_does_not_touch_other_claims(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        two_overlapping(rig)
        rig.add("far", minutes=5, created=START + timedelta(days=3))
        rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)
        [before] = claims(rig, "a")

        rig.inspections.results["far"] = inspection(
            "far", created=START + timedelta(days=4), fingerprint="fp-2"
        )
        rig.detection.analyze(AnalyzeAssetCommand("far", frozenset({OVERLAP})), CTX)

        [after] = claims(rig, "a")
        assert (after.updated_at, after.status) == (before.updated_at, before.status)

    def test_reanalysis_with_unchanged_evidence_does_not_churn_the_claim(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        two_overlapping(rig)
        rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)
        [before] = claims(rig, "a")
        with closing(sqlite3.connect(rig.path)) as conn, conn:
            conn.execute("DELETE FROM analysis_marks")  # force the analysis to run again
        rig.clock = type(rig.clock)()  # a clock that has moved on cannot be the reason it stays
        rig.detection.detect(DetectCommand(frozenset({OVERLAP}), full=True), CTX)

        [after] = claims(rig, "a")
        assert (after.updated_at, len(after.evidence)) == (before.updated_at, len(before.evidence))

    def test_a_rejected_pair_stays_rejected_through_reruns_and_version_changes(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        two_overlapping(rig)
        rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)
        [claim] = claims(rig, "a")
        stored = rig.repository.get(claim.id)
        assert stored is not None
        stored.reject(now=rig.clock.now(), note="different shoots")
        rig.repository.update(claim.id, lambda _e: stored)

        newer = Rig(tmp_path, detectors={OVERLAP: V2Overlap()}, share=rig)
        for asset in ("a", "b"):
            newer.detection.analyze(AnalyzeAssetCommand(asset, frozenset({OVERLAP})), CTX)
        rig.reconciler(DetectorVersions({OVERLAP: V2Overlap()})).reconcile(
            ReconcileCommand(force=True), CTX
        )

        [after] = claims(rig, "a")
        assert after.status is RelationshipStatus.REJECTED  # never revived by any of it


# --- failure and recovery ------------------------------------------------------------------
class Broken:
    """An inspection catalog whose answer for chosen assets is a store failure."""

    def __init__(self, inner, *bad: str) -> None:  # type: ignore[no-untyped-def]
        self.inner, self.bad, self.calls = inner, set(bad), 0

    def find(self, asset_id: str):  # type: ignore[no-untyped-def]
        self.calls += 1
        if asset_id in self.bad:
            raise PersistenceError("the store is unreadable")
        return self.inner.find(asset_id)


class TestRecovery:
    def build(self, tmp_path: Path, count: int = 5) -> Rig:
        rig = Rig(tmp_path)
        for i in range(count):
            rig.add(f"x{i}", minutes=i, created=START + timedelta(seconds=20 * i))
        return rig

    def test_one_bad_asset_does_not_block_the_walk_and_is_retried_by_the_sweep(
        self, tmp_path: Path
    ) -> None:
        rig = self.build(tmp_path)
        healthy = rig.inspections
        rig.detection._inspections = Broken(healthy, "x1")

        first = rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)

        assert isinstance(first, Ok)
        assert first.value.status is DetectStatus.PARTIAL
        assert first.value.reason is DetectReason.ASSET_FAILURES
        assert first.value.failed == 1 and "x1" in (first.value.first_error or "")
        assert first.value.analyzed == 4  # the rest were done
        assert not first.value.sweep_completed  # the failed asset means the first pass is not done
        assert rig.store.get_mark("x1", OVERLAP) is None  # no mark: it was never analysed

        rig.detection._inspections = healthy  # the cause is gone
        second = rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)

        assert isinstance(second, Ok) and second.value.status is DetectStatus.COMPLETED
        assert second.value.sweep_completed
        assert rig.store.get_mark("x1", OVERLAP) is not None
        assert len(claims(rig, "x1")) >= 1

    def test_an_outage_fails_the_job_visibly_and_leaves_the_cursor_where_it_was(
        self, tmp_path: Path
    ) -> None:
        rig = self.build(tmp_path)
        healthy = rig.inspections
        rig.detection._inspections = Broken(healthy, *(f"x{i}" for i in range(5)))

        with pytest.raises(PersistenceError):
            rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)

        last = rig.detection.last_run()
        assert last is not None and last.status is DetectStatus.FAILED
        assert last.feed_cursor is None  # nothing was skipped past
        rig.detection._inspections = healthy

        recovered = rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)

        assert isinstance(recovered, Ok) and recovered.value.analyzed == 5
        assert recovered.value.failed == 0

    def test_cancelling_mid_walk_keeps_finished_assets_and_resumes_cleanly(
        self, tmp_path: Path
    ) -> None:
        rig = self.build(tmp_path, count=6)
        ctx = JobContext.detached()
        seen: list[str] = []
        real = rig.inspections.find

        def find(asset_id: str):  # type: ignore[no-untyped-def]
            seen.append(asset_id)
            if len(seen) == 3:
                ctx.cancellation.cancel()
            return real(asset_id)

        rig.detection._inspections = type("F", (), {"find": staticmethod(find)})()

        with pytest.raises(OperationCancelledError):
            rig.detection.detect(DetectCommand(frozenset({OVERLAP})), ctx)
        rig.detection._inspections = rig.inspections
        again = rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)

        assert isinstance(again, Ok) and again.value.status is DetectStatus.COMPLETED
        assert {m for m in (f"x{i}" for i in range(6)) if rig.store.get_mark(m, OVERLAP)} == {
            f"x{i}" for i in range(6)
        }
        pairs = {(c.asset_a, c.asset_b) for i in range(6) for c in claims(rig, f"x{i}")}
        assert len(pairs) == len(set(pairs))  # no duplicate claims after the interruption

    def test_the_budget_really_bounds_the_work(self, tmp_path: Path) -> None:
        from media_house.modules.asset_relationships.application.detect_relationships import (
            DetectionSettings,
        )

        rig = Rig(tmp_path, settings=DetectionSettings(batch_assets=2, max_assets_per_job=3))
        for i in range(8):
            rig.add(f"y{i}", minutes=i, created=START + timedelta(hours=3 * i))

        job = rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)

        assert isinstance(job, Ok) and job.value.assets_visited == 3
        assert job.value.reason is DetectReason.BUDGET


# --- races ---------------------------------------------------------------------------------
@pytest.mark.parametrize("round_", range(12))
def test_both_ends_analysed_at_the_same_moment_make_exactly_one_claim(
    tmp_path: Path, round_: int
) -> None:
    rig = Rig(tmp_path)
    two_overlapping(rig)
    gate = threading.Barrier(2)
    errors: list[BaseException] = []

    def analyse(asset: str) -> None:
        try:
            gate.wait(timeout=10)
            rig.detection.analyze(
                AnalyzeAssetCommand(asset, frozenset({OVERLAP})), JobContext.detached()
            )
        except BaseException as exc:  # noqa: BLE001  # the test reports whatever a thread raised
            errors.append(exc)

    threads = [threading.Thread(target=analyse, args=(a,)) for a in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    [claim] = claims(rig, "a")
    assert claim.status is RelationshipStatus.PROPOSED and len(claim.current_evidence) == 1
    marks = [rig.store.get_mark(a, OVERLAP) for a in ("a", "b")]
    assert all(m is not None for m in marks)
    # Whichever ran second saw the other; if both missed each other, a repeat finds the pair.
    rig.detection.analyze(AnalyzeAssetCommand("a", frozenset({OVERLAP})), CTX)
    assert len(claims(rig, "a")) == 1


def test_a_decision_made_while_detection_flags_claims_is_never_lost(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    two_overlapping(rig)
    rig.detection.detect(DetectCommand(frozenset({OVERLAP})), CTX)
    [claim] = claims(rig, "a")
    gate = threading.Barrier(2)

    def confirm() -> None:
        gate.wait(timeout=10)

        def change(existing: AssetRelationship | None) -> AssetRelationship | None:
            if existing is None:
                return None
            try:
                existing.confirm(now=rig.clock.now(), note="same event")
            except IllegalTransition:
                return None  # detection got there first: a stale claim cannot be confirmed
            return existing

        rig.repository.update(claim.id, change)

    def move_b_away() -> None:
        gate.wait(timeout=10)
        rig.inspections.results["b"] = inspection(
            "b", created=START + timedelta(days=9), fingerprint="fp-moved"
        )
        rig.detection.analyze(AnalyzeAssetCommand("b", frozenset({OVERLAP})), JobContext.detached())

    threads = [threading.Thread(target=confirm), threading.Thread(target=move_b_away)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    [after] = claims(rig, "a")
    # Either order is legitimate, but a decision is never lost and a decided claim is never STALE.
    assert after.status in {RelationshipStatus.CONFIRMED, RelationshipStatus.STALE}
    if after.status is RelationshipStatus.STALE:
        assert not after.decided
    else:
        assert after.decided
