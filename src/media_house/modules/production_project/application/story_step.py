"""Step 3 of the wizard: concepts, the script, facts, Technik-Slots, versions and the live lint.

A model helps where it is allowed and able; every model result is validated, and the
deterministic path (the scaffold) always works without one.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from media_house.core.application.ports import Clock
from media_house.modules.content_intelligence.application.contracts import TextGenerator
from media_house.modules.production_project.application.projects import ProjectCatalog
from media_house.modules.production_project.application.structured import (
    check_allowed,
    generate_structured,
)
from media_house.modules.production_project.application.views import (
    ScriptReport,
    parsed_script,
    rows_of,
    script_report,
    style_facts_of,
)
from media_house.modules.production_project.domain.brief import Source
from media_house.modules.production_project.domain.compliance import Override
from media_house.modules.production_project.domain.concepts import (
    StoryConcept,
    distinct,
    mix,
    problems,
)
from media_house.modules.production_project.domain.facts import (
    derive,
    referenced_keys,
    spec_ratio_formulas,
    standard_formulas,
    verify,
    with_value,
)
from media_house.modules.production_project.domain.project import (
    Generated,
    Project,
    approve,
    restore_script,
    set_blocks,
    stamp,
    touch,
    with_script,
)
from media_house.modules.production_project.domain.prompts import (
    PROMPT_VERSION,
    LlmConcept,
    LlmConcepts,
    LlmScene,
    LlmScript,
    concepts_prompt,
    deterministic_concepts,
    rewrite_prompt,
    script_prompt,
    specs_of,
)
from media_house.modules.production_project.domain.scaffold import (
    ScaffoldInputs,
    SceneSpec,
    scaffold_script,
    script_from_specs,
)
from media_house.modules.production_project.domain.script import Scene, Script
from media_house.modules.production_project.domain.script_tools import heading_title, render_script
from media_house.modules.production_project.domain.slots import ensure_slots, fill, slot_facts
from media_house.modules.production_project.domain.values import Artifact, Severity
from media_house.modules.style_memory.application.contracts import (
    Category,
    EventKind,
    Guidance,
    GuidanceQuery,
    PreferenceEvent,
    PreferenceRecorder,
    StyleGuidance,
    Task,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import (
    ConflictError,
    Err,
    InvariantViolation,
    NotFoundError,
    Ok,
    Result,
    ValidationError,
)
from media_house.shared.types import new_id

_Failure = NotFoundError | ConflictError | ValidationError
_INFO: Final = "deterministic"


def _generation_hash(project: Project, guidance: Guidance) -> str:
    inputs = (
        project.brief.source_digest if project.brief else "",
        project.ideas,
        project.chosen_concept,
        project.concepts,
        project.facts,
        project.slots,
        guidance.fingerprint,
        PROMPT_VERSION,
    )
    return hashlib.sha256(repr(inputs).encode("utf-8")).hexdigest()


def _query(project: Project, task: Task, *, use_learned: bool = True) -> GuidanceQuery:
    return GuidanceQuery(
        task,
        project.settings.format,
        client=project.settings.client,
        project_id=project.project_id,
        use_learned=use_learned,
    )


def _evidence_pool(guidance: Guidance, rituals: Sequence[tuple[str, str]]) -> dict[str, str]:
    """The evidence ids a concept may cite, each with what it is: videos of the channel, style
    rules, and rituals the creator named."""
    pool: dict[str, str] = {}
    for hook in guidance.profile.hook_patterns:
        for p in hook.evidence:
            pool[p.source] = hook.description
    for rule in guidance.rules:
        pool[f"rule:{rule.rule_id}"] = rule.text
    for ritual_id, text in rituals:
        pool[f"ritual:{ritual_id}"] = text
    return pool


class Brainstorm:
    """Three distinct story concepts, each tied to evidence from this creator."""

    def __init__(
        self,
        projects: ProjectCatalog,
        style: StyleGuidance,
        clock: Clock,
        generator: TextGenerator | None,
    ) -> None:
        self._projects = projects
        self._style = style
        self._clock = clock
        self._generator = generator

    def execute(
        self,
        project_id: str,
        *,
        rituals: Sequence[tuple[str, str]] = (),
        use_learned: bool = True,
        cancellation: CancellationToken | None = None,
    ) -> Result[Project, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if project.brief is None:
            return Err(ValidationError("no brief", user_message="Lies zuerst ein Briefing ein."))
        guidance = self._style.guidance(_query(project, Task.CONCEPT, use_learned=use_learned))
        pool = _evidence_pool(guidance, rituals)
        rows = rows_of(project)
        concepts: list[StoryConcept] = []
        generator = _INFO
        note = ""
        if self._generator is not None and cancellation is not None:
            allowed = check_allowed(project.settings, self._generator)
            if isinstance(allowed, Err):
                if isinstance(allowed.error, ConflictError):
                    return Err(allowed.error)
                note = allowed.error.reason
            else:
                system, user = concepts_prompt(
                    guidance=guidance.render(),
                    brief=project.brief,
                    rows=rows,
                    ideas=project.ideas,
                    evidence=[f"{k}: {v}" for k, v in pool.items()],
                    brief_text=project.brief_text,
                )
                answer = generate_structured(
                    self._generator, system, user, LlmConcepts, cancellation
                )
                if isinstance(answer, Ok):
                    generator = self._generator.identity()
                    concepts = [
                        c
                        for k, raw in enumerate(answer.value.concepts)
                        if not problems(c := _concept(raw, k), set(pool))
                    ]
                    if not distinct(concepts):
                        concepts = concepts[:1]
                else:
                    note = answer.error.reason
        if len(concepts) < 3:
            hooks = [
                (h.name, h.description, [p.source for p in h.evidence])
                for h in guidance.profile.hook_patterns
            ]
            fallback = deterministic_concepts(
                hooks=hooks,
                rituals=list(rituals),
                rows=rows,
                client=project.settings.client or project.brief.client,
                brief_text=project.brief_text,
                catchphrase=guidance.profile.catchphrases[0]
                if guidance.profile.catchphrases
                else "",
            )
            concepts += list(fallback)[: 3 - len(concepts)]
        now = self._clock.now()
        record = Generated(
            Artifact.CONCEPTS,
            generator,
            PROMPT_VERSION,
            _generation_hash(project, guidance),
            now,
            guidance.applied,
            guidance.fingerprint,
        )

        def apply(p: Project) -> Project:
            changed = replace(p, concepts=tuple(concepts), chosen_concept="")
            return stamp(touch(changed, Artifact.CONCEPTS, now=now), record)

        return self._projects.update(project_id, apply, audit=("brainstorm", f"{generator} {note}"))


def _concept(raw: LlmConcept, k: int) -> StoryConcept:
    return StoryConcept(
        f"llm-{k + 1}",
        raw.logline,
        raw.hook,
        raw.red_thread,
        raw.retention,
        raw.callback,
        raw.why_you,
        raw.evidence,
        raw.titles,
    )


class ChooseConcept:
    """Pick, or mix pieces of several; the pick is a signal the memory learns from."""

    def __init__(
        self, projects: ProjectCatalog, recorder: PreferenceRecorder, clock: Clock
    ) -> None:
        self._projects = projects
        self._recorder = recorder
        self._clock = clock

    def execute(
        self,
        project_id: str,
        concept_id: str,
        *,
        why: str = "",
        mix_parts: Sequence[tuple[str, str]] = (),
    ) -> Result[Project, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        by_id = {c.concept_id: c for c in found.value.concepts}
        if concept_id not in by_id:
            return Err(NotFoundError("no concept", user_message="Dieses Konzept gibt es nicht."))
        chosen = by_id[concept_id]
        new_concepts: tuple[StoryConcept, ...] = found.value.concepts
        if mix_parts:
            parts = [(by_id[cid], field) for cid, field in mix_parts if cid in by_id]
            chosen = mix([(chosen, "logline"), *parts], f"mix-{new_id()[:6]}")
            new_concepts = (*new_concepts, chosen)
        now = self._clock.now()

        def apply(p: Project) -> Project:
            changed = replace(p, concepts=new_concepts, chosen_concept=chosen.concept_id)
            return touch(changed, Artifact.CONCEPTS, now=now)

        saved = self._projects.update(
            project_id, apply, audit=("choose_concept", chosen.concept_id)
        )
        if isinstance(saved, Ok):
            self._recorder.record(
                PreferenceEvent(
                    f"concept-{project_id[:8]}-{chosen.concept_id}",
                    EventKind.CONCEPT_PICKED,
                    now,
                    project_id,
                    "",
                    "",
                    saved.value.settings.format,
                    saved.value.settings.client,
                    Category.STRUCTURE,
                    "",
                    why or chosen.logline,
                )
            )
        return saved


class GenerateScript:
    """The script of the chosen concept: a model writes it when allowed, else the scaffold."""

    def __init__(
        self,
        projects: ProjectCatalog,
        style: StyleGuidance,
        clock: Clock,
        generator: TextGenerator | None,
    ) -> None:
        self._projects = projects
        self._style = style
        self._clock = clock
        self._generator = generator

    def execute(
        self,
        project_id: str,
        *,
        use_learned: bool = True,
        use_model: bool = True,
        cancellation: CancellationToken | None = None,
    ) -> Result[Project, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if project.brief is None:
            return Err(ValidationError("no brief", user_message="Lies zuerst ein Briefing ein."))
        guidance = self._style.guidance(_query(project, Task.SCRIPT, use_learned=use_learned))
        rows = rows_of(project)
        concept = project.concept()
        script: Script | None = None
        generator = _INFO
        if use_model and concept and self._generator is not None and cancellation is not None:
            allowed = check_allowed(project.settings, self._generator)
            if isinstance(allowed, Err):
                if isinstance(allowed.error, ConflictError):
                    return Err(allowed.error)
            else:
                fmt = guidance.profile.format(project.settings.format)
                system, user = script_prompt(
                    guidance=guidance.render(),
                    brief=project.brief,
                    rows=rows,
                    concept=concept,
                    slot_names=[s.key for s in project.slots],
                    structure=fmt.structure if fmt else (),
                    ideas=project.ideas,
                    brief_text=project.brief_text,
                )
                answer = generate_structured(
                    self._generator,
                    system,
                    user,
                    LlmScript,
                    cancellation,
                    validate=lambda candidate: _script_problem(candidate, project, guidance),
                )
                if isinstance(answer, Ok) and answer.value.scenes:
                    candidate = script_from_specs(specs_of(answer.value))
                    if _fact_refs_exist(candidate, project):
                        script = candidate
                        generator = self._generator.identity()
        if script is None:
            catch = guidance.profile.catchphrases[0] if guidance.profile.catchphrases else ""
            script = scaffold_script(
                ScaffoldInputs(
                    project.settings.title,
                    rows,
                    project.facts,
                    project.brief,
                    concept,
                    catch,
                    spedition_block=any(
                        "spedition" in e.text.casefold() for e in project.brief.optional
                    ),
                )
            )
        style = style_facts_of(guidance)
        text = render_script(
            script,
            title=project.settings.title,
            facts=project.facts,
            slot_rows=[(s.key, s.label, s.value) for s in project.slots],
            wpm=style.wpm,
        )
        now = self._clock.now()
        record = Generated(
            Artifact.SCRIPT,
            generator,
            PROMPT_VERSION,
            _generation_hash(project, guidance),
            now,
            guidance.applied,
            guidance.fingerprint,
        )

        def apply(p: Project) -> Project:
            changed = replace(
                with_script(p, text, f"Erzeugt ({generator})", now=now),
                slots=ensure_slots(p.slots, text),
            )
            return stamp(changed, record)

        return self._projects.update(project_id, apply, audit=("generate_script", generator))


def _fact_refs_exist(script: Script, project: Project) -> bool:
    keys = {f.key for f in project.facts} | {
        f.key
        for f in derive(
            project.facts, [*standard_formulas(project.facts), *spec_ratio_formulas(project.facts)]
        )
    }
    wanted = {k for sc in script.scenes for s in sc.sentences for k in referenced_keys(s.text)}
    return wanted <= keys


def _script_problem(raw: LlmScript, project: Project, guidance: Guidance) -> str:
    scenes = raw.scenes
    ids = [scene.scene_id for scene in scenes]
    if len(scenes) < 3 or ids != [f"S{k:02d}" for k in range(1, len(scenes) + 1)]:
        return "Szenen müssen vollständig, eindeutig und fortlaufend S01, S02 … heißen."
    candidate = script_from_specs(specs_of(raw))
    if not _fact_refs_exist(candidate, project):
        return "Das Script verwendet nicht vorhandene Fakten-Schlüssel."
    blob = " ".join(f"{s.title} {' '.join(s.spoken)}" for s in scenes).casefold()
    missing = [row.name for row in rows_of(project) if row.name.casefold() not in blob]
    if missing:
        return "Diese Artikel fehlen: " + ", ".join(missing)
    for row in rows_of(project):
        coverage = [
            scene
            for scene in scenes
            if row.name.casefold() in scene.title.casefold()
            and scene.function == "product"
            and len(scene.spoken) >= 2
        ]
        if not coverage:
            return f"Für {row.name} fehlt eine eigene Produktszene mit konkretem Sprechtext."
    if scenes[0].function != "hook" or not scenes[0].spoken:
        return "Der Einstieg braucht einen gesprochenen Hook."
    text = render_script(candidate, title=project.settings.title)
    report = script_report(replace(project, script=text), style_facts_of(guidance))
    unsafe = [
        finding.message for finding in report.findings if finding.severity is Severity.BLOCKER
    ]
    return "; ".join(unsafe[:4])


@dataclass(frozen=True, slots=True)
class RewriteProposal:
    """A proposed change of the script: shown as a diff, applied only on "Übernehmen"."""

    scene_id: str
    instruction: str
    before: str
    after: str


class EditScript:
    """Hand edits, versions with restore, per-scene rewrite with an instruction."""

    def __init__(
        self,
        projects: ProjectCatalog,
        style: StyleGuidance,
        recorder: PreferenceRecorder,
        clock: Clock,
        generator: TextGenerator | None,
    ) -> None:
        self._projects = projects
        self._style = style
        self._recorder = recorder
        self._clock = clock
        self._generator = generator

    def set_text(
        self, project_id: str, text: str, label: str = "Von Hand bearbeitet"
    ) -> Result[Project, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        before = found.value.script
        now = self._clock.now()
        saved = self._projects.update(
            project_id,
            lambda p: replace(
                with_script(p, text, label, now=now), slots=ensure_slots(p.slots, text)
            ),
            audit=("edit_script", label),
        )
        if isinstance(saved, Ok) and before and before != text:
            self._recorder.record(
                PreferenceEvent(
                    f"edit-{project_id[:8]}-{new_id()[:6]}",
                    EventKind.MANUAL_EDIT,
                    now,
                    project_id,
                    "",
                    "",
                    saved.value.settings.format,
                    saved.value.settings.client,
                    Category.VOICE,
                    "",
                    "",
                    before=before[:2000],
                    after=text[:2000],
                )
            )
        return saved

    def restore(self, project_id: str, version: int) -> Result[Project, _Failure]:
        now = self._clock.now()

        def apply(p: Project) -> Project:
            try:
                return restore_script(p, version, now=now)
            except InvariantViolation:
                return p

        return self._projects.update(project_id, apply, audit=("restore_script", str(version)))

    def rewrite_scene(
        self,
        project_id: str,
        scene_id: str,
        instruction: str,
        cancellation: CancellationToken,
    ) -> Result[Project, _Failure]:
        """Propose and apply at once (scripts and the CLI; the UI proposes first)."""
        proposed = self.propose(project_id, scene_id, instruction, cancellation)
        if isinstance(proposed, Err):
            return proposed
        return self.apply(project_id, proposed.value)

    def propose(
        self,
        project_id: str,
        scene_id: str,
        instruction: str,
        cancellation: CancellationToken,
    ) -> Result[RewriteProposal, _Failure]:
        """ "Neu mit Anweisung": a model rewrites ONE scene; nothing is saved until applied."""
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if self._generator is None:
            return Err(
                ValidationError("no model", user_message="Dafür braucht es ein Sprachmodell.")
            )
        allowed = check_allowed(project.settings, self._generator)
        if isinstance(allowed, Err):
            reason = allowed.error
            return Err(
                reason
                if isinstance(reason, ConflictError)
                else ValidationError(reason.reason, user_message=reason.reason)
            )
        script = parsed_script(project)
        scene = next((s for s in script.scenes if s.scene_id == scene_id), None)
        if scene is None or project.brief is None:
            return Err(NotFoundError("no scene", user_message="Diese Szene gibt es nicht."))
        guidance = self._style.guidance(_query(project, Task.SCENE_REWRITE))
        system, user = rewrite_prompt(
            guidance=guidance.render(),
            scene="\n".join(f"{s.text}" for s in scene.sentences),
            instruction=instruction,
            constraints="\n".join(f"- {c.text}" for c in project.brief.constraints),
        )
        answer = generate_structured(self._generator, system, user, LlmScene, cancellation)
        if isinstance(answer, Err):
            return Err(ValidationError(answer.error.reason, user_message=answer.error.reason))
        new = answer.value
        if new.scene_id != scene_id:
            return Err(
                ValidationError(
                    "scene id changed",
                    user_message="Der Vorschlag verändert die Szenenkennung. Hol ihn bitte neu.",
                )
            )
        specs = [
            SceneSpec(
                new.scene_id or scene_id,
                new.title,
                new.intent,
                new.function,
                new.graphics,
                new.spoken,
            )
            if s.scene_id == scene_id
            else _spec_of(s)
            for s in script.scenes
        ]
        text = render_script(
            script_from_specs(specs),
            title=project.settings.title,
            facts=project.facts,
            slot_rows=[(s.key, s.label, s.value) for s in project.slots],
        )
        if not _fact_refs_exist(script_from_specs(specs), project):
            return Err(
                ValidationError(
                    "unknown facts",
                    user_message="Der Vorschlag enthält unbekannte Fakten. Hol ihn bitte neu.",
                )
            )
        return Ok(RewriteProposal(scene_id, instruction, project.script, text))

    def apply(self, project_id: str, proposal: RewriteProposal) -> Result[Project, _Failure]:
        """ "Übernehmen": the proposal becomes the script (a version) and teaches the memory."""
        now = self._clock.now()
        current = self._projects.get(project_id)
        if isinstance(current, Err):
            return current
        if current.value.script != proposal.before:
            return Err(
                ConflictError(
                    "script changed since proposal",
                    user_message="Dein Script hat sich geändert. Hol den Vorschlag bitte neu.",
                )
            )
        label = f"{proposal.scene_id}: {proposal.instruction[:40]}"
        saved = self._projects.update(
            project_id,
            lambda p: replace(
                with_script(p, proposal.after, label, now=now),
                slots=ensure_slots(p.slots, proposal.after),
            ),
            audit=("rewrite_scene", proposal.scene_id),
        )
        if isinstance(saved, Ok):
            self._recorder.record(
                PreferenceEvent(
                    f"rewrite-{project_id[:8]}-{new_id()[:6]}",
                    EventKind.REGENERATE_INSTRUCTION,
                    now,
                    project_id,
                    proposal.scene_id,
                    "",
                    saved.value.settings.format,
                    saved.value.settings.client,
                    Category.VOICE,
                    "",
                    proposal.instruction,
                )
            )
        return saved


def _spec_of(scene: Scene) -> SceneSpec:
    lines: list[str] = []
    open_block = ""
    for s in scene.sentences:
        want = (
            f"[{s.gate}]"
            if s.gate
            else (
                f"[OPTIONAL: {s.block}]"
                if s.optional and s.block
                else ("[OPTIONAL]" if s.optional else "")
            )
        )
        if want != open_block:
            if open_block:
                lines.append(
                    "[/OPTIONAL]"
                    if open_block.startswith("[OPTIONAL")
                    else "[/" + open_block.strip("[]").split(":")[0] + "]"
                )
            if want:
                lines.append(want)
            open_block = want
        lines.append(s.text + (" [VERIFY]" if s.verify else ""))
    if open_block:
        lines.append(
            "[/OPTIONAL]"
            if open_block.startswith("[OPTIONAL")
            else "[/" + open_block.strip("[]").split(":")[0] + "]"
        )
    return SceneSpec(
        scene.scene_id,
        heading_title(scene.heading),
        scene.intent,
        scene.function,
        scene.graphics_intent,
        tuple(lines),
    )


class EditFacts:
    """Fact table and Technik-Slots: fill, change, verify, approve, switch blocks. Every change
    marks what was written down from it dirty."""

    def __init__(self, projects: ProjectCatalog, clock: Clock) -> None:
        self._projects = projects
        self._clock = clock

    def fill_slot(self, project_id: str, key: str, value: str) -> Result[Project, _Failure]:
        now = self._clock.now()

        def apply(p: Project) -> Project:
            slots = fill(p.slots, key, value)
            base = tuple(f for f in p.facts if f.source is None or f.source.ref != "Technik-Slot")
            facts = derive(
                (*base, *slot_facts(slots)),
                (*standard_formulas(base), *spec_ratio_formulas((*base, *slot_facts(slots)))),
            )
            return touch(replace(p, slots=slots, facts=facts), Artifact.SLOTS, now=now)

        try:
            return self._projects.update(project_id, apply, audit=("fill_slot", key))
        except InvariantViolation as exc:
            return Err(ValidationError(str(exc), user_message=exc.user_message))

    def set_fact(self, project_id: str, key: str, value: str) -> Result[Project, _Failure]:
        now = self._clock.now()
        return self._projects.update(
            project_id,
            lambda p: touch(
                replace(p, facts=with_value(p.facts, key, value)), Artifact.FACTS, now=now
            ),
            audit=("set_fact", key),
        )

    def verify_fact(
        self, project_id: str, key: str, source_ref: str, quote: str
    ) -> Result[Project, _Failure]:
        if not source_ref.strip() or not quote.strip():
            return Err(
                ValidationError(
                    "a verification needs its source",
                    user_message="Ohne Quelle gilt nichts als geprüft: Fundstelle und Wortlaut.",
                )
            )
        now = self._clock.now()
        return self._projects.update(
            project_id,
            lambda p: touch(
                replace(p, facts=verify(p.facts, key, Source(source_ref.strip(), quote.strip()))),
                Artifact.FACTS,
                now=now,
            ),
            audit=("verify_fact", key),
        )

    def approve(self, project_id: str, gate: str) -> Result[Project, _Failure]:
        now = self._clock.now()
        return self._projects.update(
            project_id, lambda p: approve(p, gate, now=now), audit=("approve", gate)
        )

    def set_block(self, project_id: str, block: str, on: bool) -> Result[Project, _Failure]:
        now = self._clock.now()

        def apply(p: Project) -> Project:
            off = set(p.off_blocks)
            (off.discard if on else off.add)(block)
            return set_blocks(p, off, now=now)

        return self._projects.update(project_id, apply, audit=("block", f"{block}={on}"))

    def override(
        self, project_id: str, finding_id: str, reason: str, *, remember_for_client: bool = False
    ) -> Result[Project, _Failure]:
        if not reason.strip():
            return Err(
                ValidationError(
                    "an override needs a reason",
                    user_message="Wenn du es bewusst behältst, schreib kurz warum.",
                )
            )
        now = self._clock.now()
        entry = Override(finding_id, reason.strip(), now, remember_for_client)
        return self._projects.update(
            project_id,
            lambda p: replace(
                p,
                overrides=(*(o for o in p.overrides if o.finding_id != finding_id), entry),
                updated_at=now,
            ),
            audit=("override", f"{finding_id}: {reason[:80]}"),
        )


class LintScript:
    """The live lint and the length estimate (cheap, no model)."""

    def __init__(self, projects: ProjectCatalog, style: StyleGuidance) -> None:
        self._projects = projects
        self._style = style

    def execute(
        self, project_id: str, *, text: str | None = None
    ) -> Result[ScriptReport, NotFoundError]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if text is not None:
            project = replace(project, script=text)
        guidance = self._style.guidance(_query(project, Task.SCRIPT))
        return Ok(script_report(project, style_facts_of(guidance)))
