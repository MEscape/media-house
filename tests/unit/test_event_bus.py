from dataclasses import dataclass

import pytest

from media_house.shared.events import ApplicationEvent, DomainEvent, EventBus
from tests.support.fakes import T0


@dataclass(frozen=True, kw_only=True)
class SomethingHappened(DomainEvent):
    detail: str = ""


def test_subscribers_receive_matching_events_in_order() -> None:
    bus = EventBus()
    seen: list[tuple[str, str]] = []
    bus.subscribe(SomethingHappened, lambda e: seen.append(("first", e.detail)))
    bus.subscribe(SomethingHappened, lambda e: seen.append(("second", e.detail)))
    bus.publish(SomethingHappened(occurred_at=T0, detail="x"))
    assert seen == [("first", "x"), ("second", "x")]


def test_subscribing_to_a_base_type_receives_subclasses_only() -> None:
    bus = EventBus()
    seen: list[DomainEvent] = []
    bus.subscribe(DomainEvent, seen.append)
    bus.publish(SomethingHappened(occurred_at=T0))
    bus.publish(ApplicationEvent(occurred_at=T0))
    assert len(seen) == 1


def test_cancelled_subscription_stops_delivery() -> None:
    bus = EventBus()
    seen: list[SomethingHappened] = []
    subscription = bus.subscribe(SomethingHappened, seen.append)
    subscription.cancel()
    subscription.cancel()  # idempotent
    bus.publish(SomethingHappened(occurred_at=T0))
    assert seen == []


def test_a_failing_handler_does_not_break_publisher_or_other_handlers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = EventBus()
    seen: list[SomethingHappened] = []

    def broken(_: SomethingHappened) -> None:
        raise RuntimeError("handler bug")

    bus.subscribe(SomethingHappened, broken)
    bus.subscribe(SomethingHappened, seen.append)
    bus.publish(SomethingHappened(occurred_at=T0))
    assert len(seen) == 1
    assert any("Event handler failed" in r.message for r in caplog.records)


def test_events_have_unique_ids() -> None:
    assert SomethingHappened(occurred_at=T0).event_id != SomethingHappened(occurred_at=T0).event_id
