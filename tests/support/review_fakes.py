"""Doubles for the review workflow: a store in memory, a catalog of built timelines, and facts."""

from collections.abc import Sequence
from dataclasses import replace

from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.review_orchestrator.application.ports import StoredSession
from media_house.modules.review_orchestrator.domain.session import AuditEntry, ReviewSession
from media_house.modules.review_orchestrator.domain.timeline import (
    SectionFacts,
    TimelineFacts,
    TimelineRef,
)
from media_house.modules.review_orchestrator.domain.values import TERMINAL_STATES
from media_house.modules.timeline_builder.application.contracts import BuiltTimeline
from media_house.shared.errors import ConflictError, Err, NotFoundError, Ok, Result
from tests.support.asset_dto import make_asset

ANCHOR = "anchor"
TIMELINE = "tl"


def make_ref(version: int = 1, content: str | None = None) -> TimelineRef:
    return TimelineRef(
        timeline_id=TIMELINE,
        version=version,
        content_hash=content or f"hash-{version}",
        anchor_asset_id=ANCHOR,
        build_asset_id=f"build-{version}",
    )


def make_facts(
    version: int = 1,
    *,
    content: str | None = None,
    sections: Sequence[tuple[str, str]] = (
        ("intro", "d-intro"),
        ("body", "d-body"),
        ("end", "d-end"),
    ),
    duration: int = 300,
    width: int = 1920,
) -> TimelineFacts:
    """A version of a three-section timeline; a section is changed by giving it another digest."""
    return TimelineFacts(
        ref=make_ref(version, content),
        width=width,
        height=1080,
        frame_rate="25/1",
        duration_frames=duration,
        sections=tuple(
            SectionFacts(sid, sid.title(), i * 100, (i + 1) * 100, digest)
            for i, (sid, digest) in enumerate(sections)
        ),
    )


class InMemoryReviewStore:
    """Returns copies, rejects a stale write, assigns audit sequences - like the real one."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[ReviewSession, int]] = {}
        self._audit: dict[str, list[AuditEntry]] = {}

    def create(self, session: ReviewSession, entries: Sequence[AuditEntry]) -> None:
        if session.session_id in self._sessions:
            raise ConflictError("exists", user_message="exists")
        self._sessions[session.session_id] = (session, 1)
        self._append(session.session_id, entries)

    def save(
        self, session: ReviewSession, entries: Sequence[AuditEntry], *, expected_revision: int
    ) -> int:
        current = self._sessions.get(session.session_id)
        if current is None or current[1] != expected_revision:
            raise ConflictError("stale", user_message="stale")
        self._sessions[session.session_id] = (session, expected_revision + 1)
        self._append(session.session_id, entries)
        return expected_revision + 1

    def get(self, session_id: str) -> StoredSession | None:
        found = self._sessions.get(session_id)
        return StoredSession(found[0], found[1]) if found else None

    def find(
        self, anchor_asset_id: str, *, timeline_id: str | None = None, active_only: bool = False
    ) -> list[ReviewSession]:
        found = [
            s
            for s, _ in self._sessions.values()
            if s.anchor_asset_id == anchor_asset_id
            and (timeline_id is None or s.timeline.timeline_id == timeline_id)
            and not (active_only and s.state in TERMINAL_STATES)
        ]
        return sorted(found, key=lambda s: (s.created_at, s.session_id), reverse=True)

    def entries(self, session_id: str, *, after: int = 0, limit: int = 500) -> list[AuditEntry]:
        return [e for e in self._audit.get(session_id, []) if e.sequence > after][:limit]

    def _append(self, session_id: str, entries: Sequence[AuditEntry]) -> None:
        log = self._audit.setdefault(session_id, [])
        for entry in entries:
            log.append(replace(entry, sequence=len(log) + 1))


class StubTimelineCatalog:
    """Serves built timelines by version; ``publish`` makes one the newest."""

    def __init__(self, first: BuiltTimeline) -> None:
        self._builds: dict[str, BuiltTimeline] = {}
        self._latest = ""
        self.publish(first)

    def publish(self, timeline: BuiltTimeline) -> str:
        asset_id = f"build-{timeline.build.build_version}"
        self._builds[asset_id] = timeline
        self._latest = asset_id
        return asset_id

    def latest(
        self, anchor_asset_id: str, *, timeline_id: str | None = None
    ) -> Result[tuple[BuiltTimeline, MediaAssetDto] | None, NotFoundError]:
        return Ok((self._builds[self._latest], make_asset(self._latest)))

    def history(
        self, anchor_asset_id: str, *, timeline_id: str | None = None
    ) -> list[MediaAssetDto]:
        return [make_asset(a) for a in self._builds]

    def load(self, build_asset_id: str) -> Result[BuiltTimeline, NotFoundError]:
        found = self._builds.get(build_asset_id)
        if found is None:
            return Err(NotFoundError(build_asset_id))
        return Ok(found)
