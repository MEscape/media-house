"""The style memory's use cases against an in-memory store: seed, guide, learn, review, transfer."""

import pytest

from media_house.modules.style_memory.application.guide import GuideStyle
from media_house.modules.style_memory.application.library import (
    ExemplarLibrary,
    ManageProfile,
    SeedStyleMemory,
    TransferStyleMemory,
)
from media_house.modules.style_memory.application.memory import (
    DistillPreferences,
    RecordPreference,
    ReviewRules,
)
from media_house.modules.style_memory.application.queries import GuidanceQuery
from media_house.modules.style_memory.domain.model import PreferenceEvent
from media_house.modules.style_memory.domain.values import (
    Category,
    EventKind,
    Scope,
    Status,
    Strength,
    Task,
)
from media_house.shared.errors import Err, NotFoundError, Ok
from tests.support.fakes import T0, FixedClock
from tests.unit.style_memory.fakes import InMemoryStyleStore


@pytest.fixture
def store() -> InMemoryStyleStore:
    s = InMemoryStyleStore()
    SeedStyleMemory(s, FixedClock()).execute()
    return s


def test_seeding_happens_once(store: InMemoryStyleStore) -> None:
    assert SeedStyleMemory(store, FixedClock()).execute() is False
    assert store.load_profile() is not None and len(store.list_rules()) >= 8


def test_the_first_session_shows_up_as_candidates_not_as_rules(store: InMemoryStyleStore) -> None:
    DistillPreferences(store, FixedClock()).execute()

    candidates = [r for r in store.list_rules() if r.status is Status.CANDIDATE]
    texts = [r.text for r in candidates]

    assert any("aus dem Kontext fließen" in t for t in texts)
    assert len(candidates) == 7
    assert all(not r.applies for r in candidates)
    scoped = {r.rule_id: r for r in candidates}["learned.alte-prospekte-logos-nicht-abdecken"]
    assert (scoped.scope, scoped.scope_key) == (Scope.CLIENT, "Lidl")


def test_guidance_applies_only_approved_rules_and_can_leave_learning_out(
    store: InMemoryStyleStore,
) -> None:
    clock = FixedClock()
    DistillPreferences(store, clock).execute()
    review = ReviewRules(store, clock)
    query = GuidanceQuery(task=Task.SCRIPT, format="ranking", client="Lidl")
    guide = GuideStyle(store)

    before = guide.guidance(query)
    assert before.learned_rule_count == 0

    assert isinstance(review.approve("learned.intro-fliesst-aus-kontext"), Ok)
    after = guide.guidance(query)
    assert after.learned_rule_count == 1
    assert after.fingerprint != before.fingerprint
    assert "learned.intro-fliesst-aus-kontext" in after.applied

    plain = guide.guidance(GuidanceQuery(task=Task.SCRIPT, format="ranking", use_learned=False))
    assert plain.learned_rule_count == 0 and plain.exemplars == ()


def test_guidance_text_marks_binding_rules_and_names_the_format(store: InMemoryStyleStore) -> None:
    text = GuideStyle(store).guidance(GuidanceQuery(Task.ASSET_PLAN, "ranking")).render()

    assert "[MUSS] Vom Creator werden nur Rohmaterial" in text
    assert "Ranking / Top-N: Claim-Hook" in text


def test_guidance_without_profile_is_a_not_found_error() -> None:
    with pytest.raises(NotFoundError):
        GuideStyle(InMemoryStyleStore()).guidance(GuidanceQuery(Task.SCRIPT, "ranking"))


def test_recording_an_event_refreshes_candidates(store: InMemoryStyleStore) -> None:
    clock = FixedClock()
    RecordPreference(store, clock).record(
        PreferenceEvent(
            "e1",
            EventKind.REGENERATE_INSTRUCTION,
            T0,
            "p9",
            "S02",
            "",
            "ranking",
            "",
            Category.TECHNICAL,
            "mehr_vergleiche",
            "Mehr Vergleiche innerhalb der fünf Produkte",
        )
    )

    rule = store.get_rule("learned.mehr-vergleiche")
    assert rule is not None and rule.status is Status.CANDIDATE


def test_review_actions_and_the_lock(store: InMemoryStyleStore) -> None:
    review = ReviewRules(store, FixedClock())
    DistillPreferences(store, FixedClock()).execute()
    rid = "learned.humor-gag-mit-callback"

    assert isinstance(review.set_locked(rid, True), Ok)
    locked = review.edit(rid, text="neu")
    assert isinstance(locked, Err) and "gesperrt" in locked.error.user_message
    assert isinstance(review.delete(rid), Err)
    review.set_locked(rid, False)
    approved = review.approve(rid, strength=Strength.MANDATORY)
    assert isinstance(approved, Ok) and approved.value.strength is Strength.MANDATORY
    assert isinstance(review.delete(rid), Ok) and store.get_rule(rid) is None
    assert isinstance(review.approve("nope"), Err)


def test_a_rejected_candidate_stays_rejected_after_more_events(store: InMemoryStyleStore) -> None:
    clock = FixedClock()
    DistillPreferences(store, clock).execute()
    review = ReviewRules(store, clock)
    review.reject("learned.weniger-dateien")
    store.append_event(
        PreferenceEvent(
            "again",
            EventKind.REGENERATE_INSTRUCTION,
            T0,
            "p2",
            "",
            "",
            "",
            "",
            Category.ASSETS,
            "weniger_dateien",
            "noch weniger Dateien",
        )
    )

    DistillPreferences(store, clock).execute()

    rule = store.get_rule("learned.weniger-dateien")
    assert rule is not None and rule.status is Status.REJECTED


def test_profile_edits_are_versions_that_can_be_restored(store: InMemoryStyleStore) -> None:
    manage = ManageProfile(store, FixedClock())
    first = store.load_profile()
    assert first is not None

    import dataclasses

    changed = manage.save(dataclasses.replace(first, greeting="Hallo"))
    assert changed.version == first.version + 1
    restored = manage.restore(first.version)
    assert isinstance(restored, Ok)
    assert restored.value.greeting == first.greeting and restored.value.version == 3
    assert list(manage.versions()) == [1, 2, 3]


def test_an_exemplar_needs_its_annotation_quotes_to_be_in_the_text(
    store: InMemoryStyleStore,
) -> None:
    library = ExemplarLibrary(store, FixedClock())

    bad = library.add(
        title="t",
        format="ranking",
        kind="script",
        body="Hallo Welt",
        annotations=[("nicht da", "x")],
    )
    good = library.add(
        title="t",
        format="ranking",
        kind="script",
        body="Hallo Welt",
        annotations=[("Hallo", "direkter Einstieg")],
    )

    assert isinstance(bad, Err) and isinstance(good, Ok)
    guide = GuideStyle(store).guidance(GuidanceQuery(Task.SCRIPT, "ranking"))
    assert [e.title for e in guide.exemplars] == ["t"]


def test_export_and_import_roundtrip_the_whole_memory(store: InMemoryStyleStore) -> None:
    DistillPreferences(store, FixedClock()).execute()
    exported = TransferStyleMemory(store).export_text()
    assert isinstance(exported, Ok)

    fresh = InMemoryStyleStore()
    imported = TransferStyleMemory(fresh).import_text(exported.value)

    assert isinstance(imported, Ok)
    assert list(fresh.list_rules()) == list(store.list_rules())
    assert len(fresh.list_events()) == len(store.list_events())
    assert fresh.load_profile() == store.load_profile()
    assert isinstance(TransferStyleMemory(fresh).import_text('{"document_type": "x"}'), Err)
    assert isinstance(TransferStyleMemory(fresh).import_text("not json"), Err)
