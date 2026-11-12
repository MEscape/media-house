"""Reality against the plan: what was really uploaded, what the script still lacks.

The plan is a hypothesis (one talking take); the creator may deliver fourteen chunks. Nothing is
rejected: the chunks are laid onto the script's scenes, retakes are noted (the last complete take
wins, the others stay as alternatives), and what is missing is named, in plain German.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from media_house.modules.production_project.domain.conform import Conformance
from media_house.modules.production_project.domain.material import AssetRequirement
from media_house.modules.production_project.domain.script import Script
from media_house.modules.production_project.domain.values import (
    MultiFile,
    Priority,
    Role,
    SlotStatus,
)


class CoverageState(StrEnum):
    COVERED = "covered"
    MISSING = "missing"
    MULTIPLE = "multiple"


@dataclass(frozen=True, slots=True)
class TakeRef:
    asset_id: str
    start: float
    end: float
    score: float


@dataclass(frozen=True, slots=True)
class SceneCoverage:
    scene_id: str
    state: CoverageState
    #: The take to use (the last complete one) and the alternatives, best first.
    chosen: TakeRef | None
    alternatives: tuple[TakeRef, ...]


@dataclass(frozen=True, slots=True)
class AssignedFile:
    name: str
    requirement_id: str
    role: Role
    asset_id: str
    #: A flag of this file is still open: it counts as present but not usable.
    flagged: bool = False


@dataclass(frozen=True, slots=True)
class Reconciliation:
    requirements: tuple[AssetRequirement, ...]
    coverage: tuple[SceneCoverage, ...]
    messages: tuple[str, ...]
    unassigned: tuple[str, ...]
    missing_required: tuple[str, ...]

    @property
    def missing_scenes(self) -> tuple[str, ...]:
        return tuple(c.scene_id for c in self.coverage if c.state is CoverageState.MISSING)


def coverage_of(
    script: Script, conformance: Conformance | None, order: Sequence[str]
) -> tuple[SceneCoverage, ...]:
    """Which scenes the recordings speak. ``order`` is the natural order of the recordings, used
    to decide which take is the last one."""
    rank = {asset: i for i, asset in enumerate(order)}
    rejected = (
        {(t.line.asset_id, t.line.start) for t in conformance.rejected} if conformance else set()
    )
    spoken = [s for s in script.scenes if any(x.usable for x in s.sentences)]
    out: list[SceneCoverage] = []
    for scene in spoken:
        takes: dict[int, list[tuple[tuple[int, float], TakeRef]]] = {}
        if conformance:
            for m in conformance.matches:
                if m.scene_id != scene.scene_id or m.sentence_index is None or m.excluded:
                    continue
                key = (rank.get(m.line.asset_id, len(rank)), m.line.start)
                takes.setdefault(m.sentence_index, []).append(
                    (key, TakeRef(m.line.asset_id, m.line.start, m.line.end, m.score))
                )
        if not takes:
            out.append(SceneCoverage(scene.scene_id, CoverageState.MISSING, None, ()))
            continue
        chosen: list[TakeRef] = []
        alternatives: list[TakeRef] = []
        for sentence in sorted(takes):
            options = sorted(takes[sentence], key=lambda kv: kv[0])
            good = [t for k, t in options if (t.asset_id, t.start) not in rejected] or [
                options[-1][1]
            ]
            chosen.append(good[-1])
            alternatives.extend(t for _, t in options if t is not good[-1])
        state = CoverageState.MULTIPLE if alternatives else CoverageState.COVERED
        first = min(chosen, key=lambda t: (rank.get(t.asset_id, len(rank)), t.start))
        out.append(SceneCoverage(scene.scene_id, state, first, tuple(alternatives)))
    return tuple(out)


def reconcile(
    requirements: Sequence[AssetRequirement],
    files: Sequence[AssignedFile],
    script: Script,
    conformance: Conformance | None,
    order: Sequence[str] = (),
) -> Reconciliation:
    coverage = coverage_of(script, conformance, order)
    missing = tuple(c.scene_id for c in coverage if c.state is CoverageState.MISSING)
    by_req: dict[str, list[AssignedFile]] = {}
    for f in files:
        by_req.setdefault(f.requirement_id, []).append(f)
    updated: list[AssetRequirement] = []
    messages: list[str] = []
    headline: list[str] = []
    for r in requirements:
        mine = by_req.get(r.requirement_id, [])
        updated.append(replace(r, status=_status(r, mine, missing)))
        if r.role is Role.TALKING and mine:
            headline.append(_talking_message(r, mine, missing))
        elif r.multi_file is MultiFile.SET and not mine and r.priority is Priority.REQUIRED:
            messages.append(f"{r.title} fehlt noch.")
    messages = [*headline, *messages]
    unassigned = tuple(f.name for f in files if not f.requirement_id)
    if unassigned:
        messages.append(
            f"{len(unassigned)} Datei(en) konnte ich keinem Platz zuordnen: bitte von Hand ziehen."
        )
    missing_required = tuple(
        r.requirement_id
        for r in updated
        if r.priority is Priority.REQUIRED and r.status in {SlotStatus.OPEN, SlotStatus.PARTIAL}
    )
    return Reconciliation(tuple(updated), coverage, tuple(messages), unassigned, missing_required)


def _status(
    r: AssetRequirement, mine: Sequence[AssignedFile], missing: tuple[str, ...]
) -> SlotStatus:
    if not mine:
        return SlotStatus.OPEN
    if all(f.flagged for f in mine):
        return SlotStatus.FLAGGED
    if r.role is Role.TALKING and missing:
        return SlotStatus.PARTIAL
    return SlotStatus.FILLED


def _talking_message(
    r: AssetRequirement, mine: Sequence[AssignedFile], missing: tuple[str, ...]
) -> str:
    count = len(mine)
    head = (
        f"Du hast {count} Talking-Dateien statt 1 hochgeladen, ich habe sie den Szenen zugeordnet."
        if count > 1
        else "Dein Talking-Take ist zugeordnet."
    )
    if not missing:
        return head + " Alle Szenen sind abgedeckt."
    scenes = ", ".join(missing)
    tail = f" Szene {scenes} fehlt noch." if len(missing) == 1 else f" Szenen {scenes} fehlen noch."
    return head + tail
