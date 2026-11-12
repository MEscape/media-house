"""The Kontext-Labor: three pipeline-context variants, their A/B/C evaluation, the winner."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from media_house.core.application.ports import Clock
from media_house.modules.production_project.application.ports import TimelineSource, VariantRunner
from media_house.modules.production_project.application.projects import ProjectCatalog
from media_house.modules.production_project.application.views import rows_of, timeline_findings
from media_house.modules.production_project.domain.brief import BriefModel
from media_house.modules.production_project.domain.context import (
    LEVELS,
    ContextInputs,
    EvalRun,
    RunMeasurements,
    Score,
    auto_scores,
    edit_variant,
    merge_ratings,
    total,
    variants,
    winner,
)
from media_house.modules.production_project.domain.facts import display
from media_house.modules.production_project.domain.project import Project, clean
from media_house.modules.production_project.domain.values import Artifact, ConstraintKind
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
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    ConflictError,
    Err,
    NotFoundError,
    Ok,
    Result,
    ValidationError,
)

_Failure = NotFoundError | ConflictError | ValidationError
_SHORT = 140
_LABEL = {
    "label.advertising": "Werbung-Label durchgehend",
    "no.third_party_brands": "keine Fremdmarken",
    "no.competitor_comparison": "keine Vergleiche mit anderen Anbietern",
    "no.negative_about_client": "nichts Negatives über den Auftraggeber",
    "no.illegal_pyro": "keine illegale Pyrotechnik",
    "publish.after_approval": "erst nach Freigabe veröffentlichen",
    "publish.not_before": "nicht vor dem Freigabetermin veröffentlichen",
    "confidential.item": "Vertrauliches nicht zeigen",
    "conditional.item": "Bedingtes nur nach Freigabe",
    "claim.no_superlatives": "keine Superlative",
}


def context_inputs(project: Project, guidance: Guidance) -> ContextInputs:
    profile = guidance.profile
    norm = profile.pacing_of(guidance.format)
    fmt = profile.format(guidance.format)
    concept = project.concept()
    by_key = {f.key: f for f in project.facts}
    rows = rows_of(project)
    fact_notes = [
        f"{f.label}: {display(f)} ({'geprüft' if f.verified else 'ungeprüft'})."
        for f in project.facts
        if f.kind not in {"product", "historical_product", "historical_price", "description"}
    ]
    brief = project.brief
    return ContextInputs(
        title=project.settings.title,
        client=project.settings.client or (brief.client if brief else ""),
        format_title=fmt.title if fmt else guidance.format,
        products=tuple(
            (
                r.name,
                display(price) if (price := by_key.get(f"price_{r.subject}")) else "Preis offen",
            )
            for r in rows
        ),
        facts=tuple(fact_notes),
        logline=concept.logline if concept else "",
        hook=concept.hook if concept else "",
        callback=concept.callback if concept else "",
        retention=concept.retention if concept else (),
        cuts_per_minute=(norm.cuts_per_minute_min, norm.cuts_per_minute_max)
        if norm
        else (0.0, 0.0),
        overlay_limit=profile.overlay_limit(guidance.format),
        allowed_transitions=profile.allowed_transitions,
        forbidden_transitions=profile.forbidden_transitions,
        caption_note=profile.caption_policy.note,
        layouts=profile.recurring_layouts,
        hard_rules=_hard_rules(brief),
        soft_rules=tuple(
            _short(c.text) for c in (brief.constraints_of(ConstraintKind.SOFT) if brief else ())
        ),
        never=tuple(rule for rule in profile.never if "thumbnail" not in rule.casefold()),
        assets=tuple((r.role.value, r.title, ",".join(r.scene_ids)) for r in project.requirements),
        style_rules=tuple(
            r.text for r in guidance.rules[:6] if "thumbnail" not in r.text.casefold()
        ),
    )


def _hard_rules(brief: BriefModel | None) -> tuple[tuple[str, str], ...]:
    """Binding rules, with confidentiality and approvals first so short contexts keep them."""
    binding = {ConstraintKind.HARD, ConstraintKind.CONFIDENTIAL, ConstraintKind.CONDITIONAL}
    rules = tuple(c for c in brief.constraints if c.kind in binding) if brief else ()
    priority = {
        ConstraintKind.CONFIDENTIAL: 0,
        ConstraintKind.CONDITIONAL: 1,
        ConstraintKind.HARD: 2,
    }
    ranked = sorted(
        rules,
        key=lambda c: (
            priority[c.kind],
            c.rule not in _LABEL,
            list(_LABEL).index(c.rule) if c.rule in _LABEL else 0,
        ),
    )
    seen: dict[tuple[str, str], None] = {}
    for c in ranked:
        label = _LABEL.get(c.rule, _short(c.text, 7))
        full = _short(c.text)
        if c.kind is ConstraintKind.CONFIDENTIAL:
            label = "Vertrauliches nicht zeigen oder teilen"
            full = "Vertraulich: Ohne Freigabe nicht zeigen oder teilen. " + full
        elif c.kind is ConstraintKind.CONDITIONAL:
            label = "Bedingtes erst nach Bestätigung verwenden"
            full = "Nur bei bestätigter Bedingung verwenden: " + full
        seen.setdefault((label, full), None)
    return tuple(seen)


def _short(text: str, words: int = 0) -> str:
    if words:
        parts = text.split()
        return text if len(parts) <= words else " ".join(parts[:words]) + " …"
    return text if len(text) <= _SHORT else text[: _SHORT - 1].rstrip() + "…"


class BuildContext:
    """The three variants from the project model, so they cannot contradict each other."""

    def __init__(self, projects: ProjectCatalog, style: StyleGuidance, clock: Clock) -> None:
        self._projects = projects
        self._style = style
        self._clock = clock

    def execute(self, project_id: str) -> Result[Project, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if project.brief is None:
            return Err(ValidationError("no brief", user_message="Lies zuerst ein Briefing ein."))
        guidance = self._style.guidance(
            GuidanceQuery(
                Task.CONTEXT,
                project.settings.format,
                client=project.settings.client,
                project_id=project_id,
            )
        )
        built = variants(context_inputs(project, guidance))
        edited = {c.level: c for c in project.contexts if c.edited}
        merged = tuple(edited.get(v.level, v) for v in built)
        now = self._clock.now()
        return self._projects.update(
            project_id,
            lambda p: clean(replace(p, contexts=merged), Artifact.CONTEXT, now=now),
            audit=("build_context", ",".join(str(v.word_count) for v in merged)),
        )


class EditContext:
    def __init__(self, projects: ProjectCatalog, clock: Clock) -> None:
        self._projects = projects
        self._clock = clock

    def execute(self, project_id: str, level: str, text: str) -> Result[Project, _Failure]:
        if level not in LEVELS:
            return Err(ValidationError("unknown level", user_message="Unbekannte Stufe."))
        now = self._clock.now()

        def apply(p: Project) -> Project:
            changed = tuple(edit_variant(v, text) if v.level == level else v for v in p.contexts)
            return replace(p, contexts=changed, updated_at=now)

        return self._projects.update(project_id, apply, audit=("edit_context", level))


class RecordEvaluation:
    """Record one run of one variant: automatic checks fill what is measurable, the creator rates
    the rest (1 to 5). The result feeds the preference memory."""

    def __init__(
        self,
        projects: ProjectCatalog,
        style: StyleGuidance,
        recorder: PreferenceRecorder,
        clock: Clock,
    ) -> None:
        self._projects = projects
        self._style = style
        self._recorder = recorder
        self._clock = clock

    def execute(
        self,
        project_id: str,
        level: str,
        scope: str,
        measurements: RunMeasurements,
        manual: Sequence[Score] = (),
        notes: str = "",
    ) -> Result[Project, _Failure]:
        if level not in LEVELS or scope not in {"excerpt", "full"}:
            return Err(ValidationError("bad run", user_message="Stufe oder Umfang unbekannt."))
        if any(not 1 <= s.value <= 5 for s in manual):
            return Err(ValidationError("rating", user_message="Bewertungen gehen von 1 bis 5."))
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        guidance = self._style.guidance(GuidanceQuery(Task.CONTEXT, project.settings.format))
        norm = guidance.profile.pacing_of(guidance.format)
        scores = merge_ratings(
            auto_scores(
                measurements,
                cuts=(norm.cuts_per_minute_min, norm.cuts_per_minute_max) if norm else (0.0, 0.0),
                overlay_limit=guidance.profile.overlay_limit(guidance.format),
            ),
            manual,
        )
        now = self._clock.now()
        run = EvalRun(
            f"run-{project_id[:8]}-{level}-{scope}-{len(project.eval_runs) + 1}",
            level,
            scope,
            scores,
            notes,
            now,
        )

        def apply(p: Project) -> Project:
            kept = tuple(r for r in p.eval_runs if (r.variant, r.scope) != (level, scope))
            return replace(p, eval_runs=(*kept, run), updated_at=now)

        saved = self._projects.update(project_id, apply, audit=("eval_run", f"{level}/{scope}"))
        if isinstance(saved, Ok):
            self._recorder.record(
                PreferenceEvent(
                    run.run_id,
                    EventKind.AB_RATING,
                    now,
                    project_id,
                    "",
                    "",
                    project.settings.format,
                    project.settings.client,
                    Category.STRUCTURE,
                    f"context_{level}",
                    notes,
                    rating=max(1, min(5, round(total(run)))),
                )
            )
        return saved


class RunVariant:
    """Run the pipeline once with one variant as its background and record what the built
    timeline shows: overlays at once, cuts per minute and rule violations are measured, the rest
    (hook, fidelity, graphics taste) stays for the creator to rate."""

    def __init__(
        self,
        projects: ProjectCatalog,
        style: StyleGuidance,
        runner: VariantRunner,
        timelines: TimelineSource,
        evaluate: RecordEvaluation,
    ) -> None:
        self._projects = projects
        self._style = style
        self._runner = runner
        self._timelines = timelines
        self._evaluate = evaluate

    def execute(
        self,
        project_id: str,
        level: str,
        paths: Sequence[Path],
        ctx: JobContext,
        *,
        scope: str = "excerpt",
        output_format: str = "16:9",
    ) -> Result[Project, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        variant = next((v for v in project.contexts if v.level == level), None)
        if variant is None:
            return Err(
                ValidationError(
                    "no variant", user_message="Diese Kontext-Stufe gibt es noch nicht: erst bauen."
                )
            )
        if project.manifest is not None:
            approved_paths = {
                entry.source_path.replace("\\", "/").casefold()
                for entry in project.manifest.usable_entries()
                if entry.source_path
            }
            if any(path.as_posix().casefold() not in approved_paths for path in paths):
                return Err(
                    ValidationError(
                        "unapproved variant inputs",
                        user_message="Für den Testlauf darfst du nur "
                        "geprüftes Projektmaterial verwenden. "
                        "Ordne neue Dateien zuerst zu und kläre offene Markierungen.",
                    )
                )
        ran = self._runner.run(paths, variant.text, output_format, ctx)
        if isinstance(ran, Err):
            return ran
        facts = self._timelines.facts(ran.value)
        if isinstance(facts, Err):
            return facts
        guidance = self._style.guidance(
            GuidanceQuery(
                Task.EDIT_PLAN,
                project.settings.format,
                client=project.settings.client,
                project_id=project_id,
            )
        )
        timeline = facts.value
        violations = (
            sum(1 for f in timeline_findings(project, timeline, guidance) if f.blocking)
            if project.brief
            else None
        )
        minutes = timeline.duration_seconds / 60.0
        measurements = RunMeasurements(
            overlay_max=timeline.max_simultaneous_overlays,
            cuts_per_minute=timeline.cuts / minutes if minutes > 0 else None,
            rule_violations=violations,
        )
        anchor = ran.value
        self._projects.update(project_id, lambda p: replace(p, timeline_anchor=anchor))
        return self._evaluate.execute(
            project_id,
            level,
            scope,
            measurements,
            notes=f"Testlauf mit Kontext {level} ({variant.word_count} Wörter)",
        )


class ChooseContext:
    """The winner becomes ``pipeline-context.md``: the best variant of the full run, else of the
    excerpt run; nothing is chosen until all three were run and rated."""

    def __init__(self, projects: ProjectCatalog, clock: Clock) -> None:
        self._projects = projects
        self._clock = clock

    def execute(self, project_id: str, *, pick: str = "") -> Result[Project, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        chosen = pick
        if not chosen:
            for scope in ("full", "excerpt"):
                chosen = winner([r for r in project.eval_runs if r.scope == scope]) or ""
                if chosen:
                    break
        if chosen not in LEVELS:
            return Err(
                ValidationError(
                    "no winner yet",
                    user_message="Noch kein Sieger: alle drei Varianten testen und bewerten.",
                )
            )
        now = self._clock.now()
        return self._projects.update(
            project_id,
            lambda p: replace(p, context_winner=chosen, updated_at=now),
            audit=("choose_context", chosen),
        )


def pipeline_context(project: Project) -> str:
    """The text of the winner, else the normal variant, else nothing."""
    level = project.context_winner or "normal"
    found = next((v for v in project.contexts if v.level == level), None)
    return found.text if found else ""
