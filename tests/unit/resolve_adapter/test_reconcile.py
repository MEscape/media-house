"""The three-way merge that protects a person's work: base (last synced), mine (Media House now),
theirs (Resolve now). Per field, never per element."""

from dataclasses import replace

from media_house.modules.resolve_adapter.domain.binding import ElementBinding, MarkerBinding
from media_house.modules.resolve_adapter.domain.live import LiveItem, LiveMarker
from media_house.modules.resolve_adapter.domain.reconcile import (
    Action,
    Decision,
    MarkerAction,
    decide,
    decide_markers,
    marker_digest,
)
from media_house.modules.resolve_adapter.domain.translate import ExpectedItem, ExpectedMarker
from media_house.modules.resolve_adapter.domain.values import (
    ChangeOrigin,
    Resolution,
)
from media_house.modules.resolve_adapter.domain.values import (
    ExecutionPolicy as P,
)

ASSETS = {"pool-1": "rec1"}


def item(**changes: object) -> ExpectedItem:
    base = ExpectedItem(
        element_id="v:a", kind="video", track_index=1, asset_id="rec1", name="A",
        start=0, duration=100, src_start=250, src_frames=100, enabled=True,
        props=(("Opacity", 100.0),), link=None, digest="d1",
    )  # fmt: skip
    return replace(base, **changes)  # type: ignore[arg-type]


def bound(expected: ExpectedItem | None = None) -> ElementBinding:
    e = expected or item()
    return ElementBinding(e.element_id, e.kind, "item-1", e.fields, e.digest)


def live(**changes: object) -> LiveItem:
    base = LiveItem(
        item_id="item-1", name="A", tag="v:a", track_kind="video", track_index=1,
        start=0, duration=100, media_item_id="pool-1", left_offset=250, enabled=True,
        properties=(("Opacity", 100.0),),
    )  # fmt: skip
    return replace(base, **changes)  # type: ignore[arg-type]


def decision(
    expected: ExpectedItem | None, base: ElementBinding | None, found: LiveItem | None,
    policy: P = P.REQUIRE_CONFLICT_REVIEW,
) -> Decision:  # fmt: skip
    return decide("v:a", expected, base, found, ASSETS, policy)


class TestNothingChanged:
    def test_a_new_element_is_created(self) -> None:
        d = decision(item(), None, None)

        assert d.action is Action.CREATE and dict(d.final)["start"] == "0"

    def test_an_element_that_is_in_place_is_kept_not_placed_again(self) -> None:
        d = decision(item(), bound(), live())

        assert d.action is Action.KEEP and not d.write and not d.kept

    def test_a_retry_after_an_interruption_finds_what_was_done(self) -> None:
        d = decision(item(), None, live())  # placed, never recorded: no base

        assert d.action is Action.KEEP


class TestOnlyMediaHouseChanged:
    def test_a_property_is_updated_in_place(self) -> None:
        d = decision(item(props=(("Opacity", 50.0),)), bound(), live())

        assert d.action is Action.UPDATE and d.write == (("prop:Opacity", "50"),)
        assert d.origin is ChangeOrigin.MEDIA_HOUSE

    def test_a_new_position_replaces_the_clip(self) -> None:
        d = decision(item(start=40), bound(), live())

        assert d.action is Action.REPLACE and dict(d.write)["start"] == "40"

    def test_a_removed_instruction_resets_the_property(self) -> None:
        d = decision(item(props=()), bound(), live(properties=(("Opacity", 100.0),)))

        assert d.action is Action.UPDATE and d.write == (("prop:Opacity", "100"),)


class TestOnlyThePersonChanged:
    def test_a_moved_clip_is_left_where_the_person_put_it(self) -> None:
        d = decision(item(), bound(), live(start=30))

        assert d.action is Action.LEAVE
        assert d.kept == (("start", "30"),) and not d.write and not d.conflicts
        assert d.origin is ChangeOrigin.RESOLVE and d.changed_by_person

    def test_a_trim_a_disabled_clip_and_a_new_opacity_are_all_theirs(self) -> None:
        d = decision(
            item(), bound(), live(duration=60, enabled=False, properties=(("Opacity", 30.0),))
        )

        assert d.action is Action.LEAVE
        assert dict(d.kept) == {"duration": "60", "enabled": "0", "prop:Opacity": "30"}

    def test_a_property_resolve_does_not_report_is_never_overwritten(self) -> None:
        d = decision(item(props=(("Opacity", 80.0),)), bound(item()), live(properties=()))

        assert "prop:Opacity" not in dict(d.write)


class TestBothChanged:
    def test_different_fields_are_merged_nothing_is_lost(self) -> None:
        mine = item(props=(("Opacity", 50.0),))  # Media House: opacity
        d = decision(mine, bound(), live(start=30))  # the person: position

        assert d.action is Action.UPDATE
        assert dict(d.write) == {"prop:Opacity": "50"}
        assert dict(d.kept) == {"start": "30"}
        assert dict(d.final)["start"] == "30" and dict(d.final)["prop:Opacity"] == "50"
        assert not d.conflicts and d.origin is ChangeOrigin.RESOLVE

    def test_the_same_change_on_both_sides_is_no_conflict(self) -> None:
        d = decision(item(start=30), bound(), live(start=30))

        assert d.action is Action.KEEP and not d.conflicts

    def test_the_same_field_changed_differently_is_a_conflict(self) -> None:
        d = decision(item(start=40), bound(), live(start=30))

        [c] = d.conflicts
        assert (c.name, c.base, c.media_house, c.resolve) == ("start", "0", "40", "30")
        assert d.origin is ChangeOrigin.BOTH

    def test_review_keeps_the_persons_version_and_holds_the_other(self) -> None:
        d = decision(item(start=40), bound(), live(start=30), P.REQUIRE_CONFLICT_REVIEW)

        assert d.action is Action.LEAVE and d.resolution is Resolution.OPEN
        assert dict(d.final)["start"] == "30"

    def test_preserve_keeps_the_persons_version_and_says_so(self) -> None:
        d = decision(item(start=40), bound(), live(start=30), P.PRESERVE_MANUAL_EDITS)

        assert d.action is Action.LEAVE and d.resolution is Resolution.KEPT_PERSON

    def test_update_managed_applies_media_house_for_the_conflicting_field_only(self) -> None:
        mine = item(start=40, props=(("Opacity", 50.0),))
        d = decision(mine, bound(), live(start=30, enabled=False), P.UPDATE_MANAGED_ELEMENTS)

        assert d.action is Action.REPLACE and d.resolution is Resolution.APPLIED_MEDIA_HOUSE
        assert dict(d.final)["start"] == "40"  # the conflict went to Media House ...
        assert dict(d.final)["enabled"] == "0"  # ... the person's other change survived
        assert d.conflicts[0].resolve == "30"  # and what was overwritten is on record


class TestDeletedAndRemoved:
    def test_a_clip_the_person_deleted_stays_deleted_while_media_house_agrees(self) -> None:
        d = decision(item(), bound(), None)

        assert d.action is Action.LEAVE and "deleted in Resolve" in d.note
        assert d.origin is ChangeOrigin.RESOLVE

    def test_deleted_by_the_person_but_changed_by_media_house_is_a_conflict(self) -> None:
        d = decision(item(start=40), bound(), None, P.REQUIRE_CONFLICT_REVIEW)

        assert d.action is Action.LEAVE and d.resolution is Resolution.OPEN and d.conflicts

    def test_update_managed_places_it_again_and_records_it(self) -> None:
        d = decision(item(start=40), bound(), None, P.UPDATE_MANAGED_ELEMENTS)

        assert d.action is Action.CREATE and d.resolution is Resolution.APPLIED_MEDIA_HOUSE

    def test_a_clip_media_house_dropped_and_nobody_touched_is_removed(self) -> None:
        d = decision(None, bound(), live())

        assert d.action is Action.DELETE and not d.conflicts

    def test_a_clip_media_house_dropped_but_the_person_changed_is_kept(self) -> None:
        d = decision(None, bound(), live(start=30), P.PRESERVE_MANUAL_EDITS)

        assert d.action is Action.LEAVE and d.resolution is Resolution.KEPT_PERSON

    def test_a_clip_gone_from_both_sides_needs_nothing(self) -> None:
        assert decision(None, bound(), None).action is Action.KEEP

    def test_a_clip_nobody_here_placed_is_not_ours_to_remove(self) -> None:
        assert decision(None, None, live()).action is Action.LEAVE


class TestMarkers:
    def mark(self, **changes: object) -> ExpectedMarker:
        base = ExpectedMarker("mh:gap:x:y", 10, "Red", "TODO", "do it", 1)
        return replace(base, **changes)  # type: ignore[arg-type]

    def there(self, m: ExpectedMarker, **changes: object) -> LiveMarker:
        base = LiveMarker("timeline", m.frame, m.color, m.name, m.note, m.duration, m.custom)
        return replace(base, **changes)  # type: ignore[arg-type]

    def test_a_new_marker_is_added(self) -> None:
        [d] = decide_markers((self.mark(),), (), ())

        assert d.action is MarkerAction.ADD

    def test_a_present_marker_is_kept(self) -> None:
        m = self.mark()

        [d] = decide_markers((m,), (MarkerBinding(m.custom, marker_digest(m)),), (self.there(m),))

        assert d.action is MarkerAction.KEEP

    def test_a_marker_the_person_removed_after_doing_the_work_does_not_come_back(self) -> None:
        m = self.mark()

        [d] = decide_markers((m,), (MarkerBinding(m.custom, marker_digest(m)),), ())

        assert d.action is MarkerAction.LEAVE

    def test_a_marker_media_house_changed_is_replaced(self) -> None:
        old, new = self.mark(), self.mark(note="newer")

        [d] = decide_markers(
            (new,), (MarkerBinding(old.custom, marker_digest(old)),), (self.there(old),)
        )

        assert d.action is MarkerAction.REPLACE

    def test_a_marker_the_person_edited_is_left_alone(self) -> None:
        m = self.mark(note="newer")
        old = self.mark()

        [d] = decide_markers(
            (m,),
            (MarkerBinding(old.custom, marker_digest(old)),),
            (self.there(old, note="my words"),),
        )

        assert d.action is MarkerAction.LEAVE

    def test_a_marker_no_longer_wanted_is_removed_only_if_untouched(self) -> None:
        old = self.mark()
        untouched = decide_markers(
            (), (MarkerBinding(old.custom, marker_digest(old)),), (self.there(old),)
        )
        edited = decide_markers(
            (), (MarkerBinding(old.custom, marker_digest(old)),), (self.there(old, name="mine"),)
        )

        assert [d.action for d in untouched] == [MarkerAction.REMOVE]
        assert [d.action for d in edited] == [MarkerAction.LEAVE]

    def test_a_persons_own_marker_is_never_looked_at(self) -> None:
        own = LiveMarker("timeline", 5, "Blue", "mine", "", 1, "")

        assert decide_markers((), (), (own,)) == ()
