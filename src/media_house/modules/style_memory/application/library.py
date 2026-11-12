"""Profile, exemplars, reference styles, transfer and the cold start."""

import json
from collections.abc import Sequence
from dataclasses import replace

from media_house.core.application.ports import Clock
from media_house.core.domain import json_codec
from media_house.modules.style_memory.application.ports import StyleStore
from media_house.modules.style_memory.domain.model import (
    Annotation,
    Exemplar,
    PacingStats,
    ReferenceCard,
    StyleMemoryDocument,
    StyleProfile,
)
from media_house.modules.style_memory.domain.references import StyleTranslation, translate
from media_house.modules.style_memory.domain.seed import (
    manual_session_events,
    mesc_profile,
    mesc_rules,
)
from media_house.shared.errors import Err, NotFoundError, Ok, Result, ValidationError
from media_house.shared.types import new_id

DOCUMENT_TYPE = "style_memory"


class SeedStyleMemory:
    """The cold start: profile, rules and the first session's events (only when still empty)."""

    def __init__(self, store: StyleStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def execute(self) -> bool:
        if self._store.load_profile() is not None:
            return False
        now = self._clock.now()
        self._store.save_profile(mesc_profile(now))
        for rule in mesc_rules(now):
            self._store.save_rule(rule)
        for event in manual_session_events(now):
            self._store.append_event(event)
        return True


class ManageProfile:
    """Read and edit the profile; every edit is a new version, older ones stay restorable."""

    def __init__(self, store: StyleStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def current(self) -> Result[StyleProfile, NotFoundError]:
        profile = self._store.load_profile()
        if profile is None:
            return Err(NotFoundError("no profile", user_message="Es gibt noch kein Stilprofil."))
        return Ok(profile)

    def save(self, profile: StyleProfile) -> StyleProfile:
        latest = self._store.load_profile()
        number = (latest.version if latest else 0) + 1
        stored = replace(profile, version=number, updated_at=self._clock.now())
        self._store.save_profile(stored)
        return stored

    def versions(self) -> Sequence[int]:
        return self._store.profile_versions()

    def restore(self, version: int) -> Result[StyleProfile, NotFoundError]:
        old = self._store.load_profile_version(version)
        if old is None:
            return Err(NotFoundError(f"no version {version}", user_message="Version fehlt."))
        return Ok(self.save(old))


class ExemplarLibrary:
    """Approved scripts as annotated few-shot examples: what makes each good."""

    def __init__(self, store: StyleStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def add(
        self,
        *,
        title: str,
        format: str,  # noqa: A002
        kind: str,
        body: str,
        annotations: Sequence[tuple[str, str]],
        tags: Sequence[str] = (),
        source: str = "",
        exemplar_id: str | None = None,
    ) -> Result[Exemplar, ValidationError]:
        if not title.strip() or not body.strip():
            return Err(
                ValidationError(
                    "empty exemplar", user_message="Titel und Text dürfen nicht leer sein."
                )
            )
        missing = [q for q, _ in annotations if q not in body]
        if missing:
            return Err(
                ValidationError(
                    f"annotation quotes not in the text: {missing}",
                    user_message="Jedes Zitat der Anmerkungen muss im Text vorkommen.",
                )
            )
        exemplar = Exemplar(
            exemplar_id=exemplar_id or f"ex.{new_id()[:12]}",
            title=title.strip(),
            format=format,
            kind=kind,
            body=body,
            annotations=tuple(Annotation(q, w) for q, w in annotations),
            tags=tuple(tags),
            approved_at=self._clock.now(),
            source=source,
        )
        self._store.save_exemplar(exemplar)
        return Ok(exemplar)

    def list(self) -> Sequence[Exemplar]:
        return self._store.list_exemplars()

    def remove(self, exemplar_id: str) -> None:
        self._store.delete_exemplar(exemplar_id)


class ReferenceStyles:
    """Reference creators: measured cards, translated into the channel's allowed vocabulary."""

    def __init__(self, store: StyleStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def add_card(
        self, *, name: str, source: str, stats: PacingStats, techniques: Sequence[str]
    ) -> ReferenceCard:
        card = ReferenceCard(
            f"ref.{new_id()[:12]}",
            name.strip(),
            source,
            stats,
            tuple(techniques),
            self._clock.now(),
        )
        self._store.save_card(card)
        return card

    def translate(
        self,
        card_id: str,
        *,
        format: str,  # noqa: A002
    ) -> Result[StyleTranslation, NotFoundError]:
        profile = self._store.load_profile()
        card = next((c for c in self._store.list_cards() if c.card_id == card_id), None)
        if profile is None or card is None:
            return Err(
                NotFoundError("missing card or profile", user_message="Vorbild nicht gefunden.")
            )
        return Ok(translate(card, profile, format=format))

    def list(self) -> Sequence[ReferenceCard]:
        return self._store.list_cards()


class TransferStyleMemory:
    """Export and import the whole style memory as one strict JSON document."""

    def __init__(self, store: StyleStore) -> None:
        self._store = store

    def export_text(self) -> Result[str, NotFoundError]:
        profile = self._store.load_profile()
        if profile is None:
            return Err(NotFoundError("no profile", user_message="Nichts zu exportieren."))
        document = StyleMemoryDocument(
            profile,
            tuple(self._store.list_rules()),
            tuple(self._store.list_events()),
            tuple(self._store.list_exemplars()),
            tuple(self._store.list_cards()),
        )
        return Ok(json_codec.dumps(DOCUMENT_TYPE, document))

    def import_text(self, text: str) -> Result[int, ValidationError]:
        try:
            raw = json.loads(text)
            if raw.pop("document_type", None) != DOCUMENT_TYPE:
                raise ValueError("not a style memory document")
            document = json_codec.decode(StyleMemoryDocument, raw)
        except (ValueError, TypeError, AttributeError) as exc:
            return Err(
                ValidationError(
                    f"unreadable style memory: {exc}",
                    user_message="Die Datei ist kein gültiger Stil-Export.",
                )
            )
        self._store.save_profile(document.profile)
        for rule in document.rules:
            self._store.save_rule(rule)
        known = {e.event_id for e in self._store.list_events()}
        for event in document.events:
            if event.event_id not in known:
                self._store.append_event(event)
        for exemplar in document.exemplars:
            self._store.save_exemplar(exemplar)
        for card in document.reference_cards:
            self._store.save_card(card)
        return Ok(len(document.rules))
