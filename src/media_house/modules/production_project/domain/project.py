"""The production project: one document, the single source of truth.

Scenes, facts, slots, constraints, asset requirements, context variants and metadata are all
derived from (or kept in) this one model. Editing one thing marks what was written down from it
dirty and offers to regenerate it, instead of silently keeping a stale copy. Every generated
artifact says what it was generated from (model, prompt version, inputs hash).

The script itself is stored as the transcript markdown: that text is canonical, its parsed form
(``parse_script``) is derived on demand and always agrees with it.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from datetime import datetime

from media_house.modules.production_project.domain.brief import BriefModel, Fact
from media_house.modules.production_project.domain.claims import ClaimRisk, RiskDecision
from media_house.modules.production_project.domain.compliance import Override, TimelineFacts
from media_house.modules.production_project.domain.concepts import StoryConcept
from media_house.modules.production_project.domain.context import ContextVariant, EvalRun
from media_house.modules.production_project.domain.facts import Formula
from media_house.modules.production_project.domain.manifest import Manifest
from media_house.modules.production_project.domain.material import AssetRequirement
from media_house.modules.production_project.domain.metadata import YoutubeMetadata
from media_house.modules.production_project.domain.slots import Slot
from media_house.modules.production_project.domain.values import DEPENDS_ON, Artifact
from media_house.shared.errors import InvariantViolation

PROJECT_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    slug: str
    title: str
    client: str
    #: The format profile (``ranking``, ``brochure_review``, ``generic`` ...).
    format: str
    year: int
    #: Confidential mode: nothing leaves the machine unless ``remote_ai_allowed``.
    confidential: bool = True
    remote_ai_allowed: bool = False
    sponsored: bool = True
    competitors: tuple[str, ...] = ()
    #: Brands the brief allows in the picture, beyond the client's own.
    allowed_brands: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Generated:
    """What an artifact was generated from, for reproducibility and dirty tracking."""

    artifact: Artifact
    #: ``deterministic`` or the model identity.
    generator: str
    prompt_version: str
    inputs_hash: str
    at: datetime
    #: The ids of the style rules and exemplars applied, and the guidance fingerprint.
    applied: tuple[str, ...] = ()
    guidance: str = ""


@dataclass(frozen=True, slots=True)
class ScriptVersion:
    version: int
    text: str
    label: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    settings: ProjectSettings
    revision: int
    created_at: datetime
    updated_at: datetime
    # --- the brief -------------------------------------------------------------------------------
    brief_text: str = ""
    brief: BriefModel | None = None
    ideas: str = ""
    reference_links: tuple[str, ...] = ()
    risks: tuple[ClaimRisk, ...] = ()
    decisions: tuple[RiskDecision, ...] = ()
    # --- the story -------------------------------------------------------------------------------
    concepts: tuple[StoryConcept, ...] = ()
    chosen_concept: str = ""
    script: str = ""
    script_versions: tuple[ScriptVersion, ...] = ()
    facts: tuple[Fact, ...] = ()
    formulas: tuple[Formula, ...] = ()
    slots: tuple[Slot, ...] = ()
    #: Approved gates (the names in the script) and conditional constraints (their ids).
    approvals: tuple[str, ...] = ()
    #: Optional blocks switched off.
    off_blocks: tuple[str, ...] = ()
    # --- material and checks ---------------------------------------------------------------------
    requirements: tuple[AssetRequirement, ...] = ()
    manifest: Manifest | None = None
    overrides: tuple[Override, ...] = ()
    tracking_link: str = ""
    #: What the final timeline does (read from the built timeline) for the timeline QC.
    timeline: TimelineFacts | None = None
    #: The anchor asset of the last timeline built from this project's material (the first
    #: recording): every run of the same recordings lands on the same anchor.
    timeline_anchor: str = ""
    metadata: YoutubeMetadata | None = None
    # --- context lab -----------------------------------------------------------------------------
    contexts: tuple[ContextVariant, ...] = ()
    eval_runs: tuple[EvalRun, ...] = ()
    context_winner: str = ""
    # --- bookkeeping -----------------------------------------------------------------------------
    generated: tuple[Generated, ...] = ()
    dirty: tuple[Artifact, ...] = ()
    schema: int = PROJECT_SCHEMA

    def concept(self) -> StoryConcept | None:
        return next((c for c in self.concepts if c.concept_id == self.chosen_concept), None)

    def is_dirty(self, artifact: Artifact) -> bool:
        return artifact in self.dirty


def dependants_of(artifact: Artifact) -> tuple[Artifact, ...]:
    """Every artifact that (transitively) rests on ``artifact``, in a fixed order."""
    found: list[Artifact] = []
    frontier = {artifact}
    while frontier:
        nxt = [
            a
            for a, deps in DEPENDS_ON.items()
            if frontier & set(deps) and a not in found and a is not artifact
        ]
        found.extend(nxt)
        frontier = set(nxt)
    return tuple(sorted(found, key=lambda a: list(Artifact).index(a)))


def touch(project: Project, changed: Artifact, *, now: datetime) -> Project:
    """Something was edited: everything derived from it is dirty (and what was edited is clean
    again, it is the source)."""
    dirty = {a for a in project.dirty if a is not changed} | set(dependants_of(changed))
    return replace(project, dirty=_ordered(dirty), updated_at=now)


def clean(project: Project, artifact: Artifact, *, now: datetime) -> Project:
    return replace(
        project, dirty=_ordered(a for a in project.dirty if a is not artifact), updated_at=now
    )


def _ordered(items: Iterable[Artifact]) -> tuple[Artifact, ...]:
    chosen = set(items)
    return tuple(a for a in Artifact if a in chosen)


def stamp(project: Project, record: Generated) -> Project:
    """Record how an artifact was generated (the newest record per artifact is kept)."""
    kept = tuple(g for g in project.generated if g.artifact is not record.artifact)
    return replace(project, generated=(*kept, record))


def with_script(project: Project, text: str, label: str, *, now: datetime) -> Project:
    """A new script text becomes a version (every edit is restorable)."""
    number = max((v.version for v in project.script_versions), default=0) + 1
    versions = (*project.script_versions, ScriptVersion(number, text, label, now))
    return touch(replace(project, script=text, script_versions=versions), Artifact.SCRIPT, now=now)


def restore_script(project: Project, version: int, *, now: datetime) -> Project:
    found = next((v for v in project.script_versions if v.version == version), None)
    if found is None:
        raise InvariantViolation(
            f"No script version {version}", user_message="Diese Version gibt es nicht."
        )
    return with_script(project, found.text, f"Wiederhergestellt: Version {version}", now=now)


def approve(project: Project, gate: str, *, now: datetime) -> Project:
    if gate in project.approvals:
        return project
    return replace(project, approvals=(*project.approvals, gate), updated_at=now)


def set_blocks(project: Project, off: Collection[str], *, now: datetime) -> Project:
    return touch(
        replace(project, off_blocks=tuple(sorted(set(off)))), Artifact.REQUIREMENTS, now=now
    )
