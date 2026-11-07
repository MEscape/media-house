# ruff: noqa: E501 - the pattern tables are one entry per line, like the rule tables elsewhere.
"""A person's words turned into instructions: what they asked for, about what, how sure we are.

The reading is deterministic (patterns, no model): the same words and the same timeline always give
the same instruction, and the original words are kept next to the reading. It separates a directive
from a preference, a question, a rejection, an approval, a request to lock and an answer to a
question; it resolves "this section", "the introduction", "the music here", "the previous shot"
from the playhead, the selection and the timeline; and when a reference could mean more than one
thing it asks instead of choosing. A clear instruction is never widened into something broader.
"""

import re
from dataclasses import dataclass, replace

from media_house.core.domain import json_codec
from media_house.modules.revision_engine.domain.facts import MixView, TimelineView
from media_house.modules.revision_engine.domain.model import Clarification, Instruction, Target
from media_house.modules.revision_engine.domain.planner import MAX_STEP_DB, Draft
from media_house.modules.revision_engine.domain.values import (
    ChangeKind,
    InstructionKind,
    Level,
    TargetKind,
)

_I = re.IGNORECASE


@dataclass(frozen=True, slots=True)
class Context:
    """Where the person was looking when they spoke."""

    timeline: TimelineView
    playhead_frame: int | None = None
    selected_clips: tuple[str, ...] = ()
    section_id: str | None = None


# --- kinds -----------------------------------------------------------------------------------------

_QUESTION = re.compile(r"^(why|what|how|when|where|who|which|is|are|does|do|did|will|was)\b", _I)
_APPROVAL = re.compile(
    r"^(yes|yep|ok(ay)?|good|great|perfect|nice|looks? good|i like (it|this)|approved?|accept(ed)?|fine)\b",
    _I,
)
_REJECTION = re.compile(
    r"^(no\b|nope|reject|don'?t (like|want|do)|do not (like|want|do)|not like that|i don'?t like)",
    _I,
)
_LOCK = re.compile(
    r"(leave|keep|have).{0,40}(exactly|just)?\s*(as it is|as is|untouched|unchanged|the way it is)|\bdon'?t (touch|change)\b|\bdo not (touch|change)\b|\block\b",
    _I,
)
_KEEP = re.compile(r"\bkeep\b", _I)
_PREFERENCE = re.compile(
    r"\b(i'?d prefer|i would prefer|i'?d like|i would like|maybe|perhaps|it would be nice|ideally|i think|if possible|a bit|slightly|a little)\b",
    _I,
)
_POLITE = re.compile(r"^(can|could|would|will) you( please)?\b", _I)

# --- intents (closed vocabulary) -------------------------------------------------------------------

_INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "music_later",
        re.compile(
            r"(music|song|track|soundtrack).{0,30}(start|begin|come in|kick in).{0,20}(later|after)|music should start later",
            _I,
        ),
    ),
    (
        "original_audio",
        re.compile(r"(original|camera|raw|production|natural) (camera )?(audio|sound)", _I),
    ),
    (
        "less_processed",
        re.compile(
            r"(too|over)[ -]?processed|sounds? (processed|robotic|unnatural|artificial)|less processed",
            _I,
        ),
    ),
    (
        "change_transition",
        re.compile(
            r"transition.{0,40}(distract|too much|ugly|annoying|hate|jarring|remove|change)|(distract|annoy).{0,30}transition|remove .{0,20}transition",
            _I,
        ),
    ),
    (
        "abrupt_ending",
        re.compile(
            r"(abrupt|sudden)|ends? too (suddenly|quickly|fast)|ending (feels|is) (too )?(abrupt|sudden|rushed)",
            _I,
        ),
    ),
    (
        "softer_style",
        re.compile(
            r"(too )?(aggressive|busy|flashy|loud|strong|much).{0,20}(caption|subtitle|graphic|animation)|(caption|subtitle|graphic|animation).{0,30}(aggressive|too busy|too flashy|too much|too strong|too loud)",
            _I,
        ),
    ),
    (
        "quieter",
        re.compile(
            r"quieter|less noticeable|too loud|lower the (volume|music|level|sound)|turn (it|the \w+) down|softer|reduce the (volume|music)|(music|sound|dialogue|voice).{0,20}(less|lower|down)\b",
            _I,
        ),
    ),
    (
        "louder",
        re.compile(
            r"louder|too quiet|can'?t hear|cannot hear|turn (it|the \w+) up|more volume|raise the (volume|music|level)|(music|sound|dialogue|voice).{0,20}(more|higher|up)\b",
            _I,
        ),
    ),
    (
        "more_dynamic",
        re.compile(
            r"more (dynamic|energetic|engaging|lively)|livelier|punchier|snappier|more energy|faster paced?",
            _I,
        ),
    ),
    (
        "shorten",
        re.compile(
            r"too long|shorter|shorten|cut (it )?down|trim|too slow|drags?|takes too long", _I
        ),
    ),
    ("lengthen", re.compile(r"too short|longer|lengthen|more time|hold (it )?longer|linger", _I)),
    ("remove", re.compile(r"\b(remove|delete|get rid of|take out|cut out|drop)\b", _I)),
    (
        "more_of",
        re.compile(
            r"\b(use|show|add|need|want|include) more\b|\bmore (?!dynamic|energetic|engaging|lively|volume|time|energy)",
            _I,
        ),
    ),
)
_MORE_OF = re.compile(
    r"\bmore (?P<what>[\w\- ]{3,40}?)(?:\s+(?:here|please|in|at|during)\b|[.,!?]|$)", _I
)

# --- targets ---------------------------------------------------------------------------------------

_ROLE_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("music", re.compile(r"\b(music|song|soundtrack|track)\b", _I)),
    (
        "dialogue",
        re.compile(r"\b(dialogue|voice|voiceover|speech|narration|narrator|talking)\b", _I),
    ),
    ("sfx", re.compile(r"\b(sound effects?|sfx|whoosh|effects?)\b", _I)),
    ("captions", re.compile(r"\b(captions?|subtitles?)\b", _I)),
    ("graphics", re.compile(r"\b(graphics?|lower thirds?|titles?|logo|animations?)\b", _I)),
)
_TIME = re.compile(
    r"\b(?:at\s+)?(?:(?P<m>\d{1,2}):(?P<s>\d{2})|(?P<sec>\d+(?:\.\d+)?)\s*(?:s|sec|seconds?))\b", _I
)
_FIRST = re.compile(r"\b(introduction|intro|opening|beginning|start|hook)\b", _I)
_LAST = re.compile(r"\b(ending|end|outro|conclusion|finale|last part|closing)\b", _I)
_HERE = re.compile(r"\b(this section|this part|here|this bit|right here)\b", _I)
_THIS_CLIP = re.compile(
    r"\b(this|that|the current|the selected) (shot|clip|reaction( shot)?|footage|cutaway|b-?roll)\b",
    _I,
)
_PREV_CLIP = re.compile(r"\b(the )?(previous|last|prior|preceding) (shot|clip)\b", _I)
_TRANSITION = re.compile(
    r"\b(this|that|the last|the previous|the) transition\b|\btransitions?\b", _I
)
_GLOBAL = re.compile(
    r"\b(everywhere|throughout|the whole (video|thing)|overall|all of it|globally)\b", _I
)

_AMOUNT_SMALL = re.compile(r"\b(a bit|slightly|a little|just a touch|somewhat)\b", _I)
_AMOUNT_LARGE = re.compile(r"\b(a lot|much|way|far|significantly|really)\b", _I)
_SECONDS = re.compile(r"\bby (?P<n>\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)\b", _I)


def _kind(clause: str) -> InstructionKind:
    text = clause.strip()
    if _LOCK.search(text):
        return InstructionKind.LOCK
    if _APPROVAL.match(text) and not any(p.search(text) for _, p in _INTENTS):
        return InstructionKind.APPROVAL
    if _REJECTION.match(text) and not any(p.search(text) for _, p in _INTENTS):
        return InstructionKind.REJECTION
    has_intent = any(p.search(text) for _, p in _INTENTS)
    if (text.endswith("?") or _QUESTION.match(text)) and not _POLITE.match(text) and not has_intent:
        return InstructionKind.QUESTION
    if _KEEP.search(text) and not has_intent:
        return InstructionKind.KEEP
    if _PREFERENCE.search(text) and has_intent:
        return InstructionKind.PREFERENCE
    if has_intent:
        return InstructionKind.DIRECTIVE
    return InstructionKind.CLARIFICATION


def _intent(clause: str) -> str:
    for name, pattern in _INTENTS:
        if pattern.search(clause):
            return name
    return ""


def _role(clause: str) -> str:
    for name, pattern in _ROLE_WORDS:
        if pattern.search(clause):
            return name
    return ""


def _clauses(text: str) -> list[str]:
    """One instruction per clause: ``;`` and full stops split, and so does ``and`` when both sides
    ask for something on their own."""
    pieces = [p.strip() for p in re.split(r"[;\n]|\.(?:\s|$)", text) if p.strip()]
    out: list[str] = []
    for piece in pieces:
        parts = re.split(r"\s+(?:and|but|also)\s+", piece, flags=_I)
        if len(parts) > 1 and all(
            _intent(p) or _kind(p) in (InstructionKind.LOCK, InstructionKind.KEEP) for p in parts
        ):
            out.extend(parts)
        else:
            out.append(piece)
    return out or [text.strip()]


def _frame_of(clause: str, ctx: Context) -> int | None:
    found = _TIME.search(clause)
    if not found:
        return None
    seconds = (
        int(found.group("m")) * 60 + int(found.group("s"))
        if found.group("m") is not None
        else float(found.group("sec"))
    )
    return round(seconds * float(ctx.timeline.fps))


def _section_by_name(clause: str, ctx: Context) -> str | None:
    lowered = clause.lower()
    for section in ctx.timeline.sections:
        name = section.name.lower()
        if len(name) > 2 and (name in lowered or section.section_id.lower() in lowered.split()):
            return section.section_id
    return None


def resolve_target(clause: str, intent: str, ctx: Context) -> tuple[Target, Clarification | None]:
    """What the words refer to, or the question that would settle it."""
    timeline = ctx.timeline
    role = _role(clause)
    frame = _frame_of(clause, ctx)
    anchor = frame if frame is not None else ctx.playhead_frame
    sections = timeline.sections
    needs_place = intent in {
        "shorten",
        "lengthen",
        "remove",
        "more_dynamic",
        "change_transition",
        "abrupt_ending",
        "original_audio",
    }

    def section_target(section_id: str, how: str) -> Target:
        section = timeline.section(section_id)
        return Target(
            kind=TargetKind.SECTION,
            section_ids=(section_id,),
            role=role,
            start_frame=section.start_frame if section else None,
            end_frame=section.end_frame if section else None,
            resolved_by=how,
        )

    if _GLOBAL.search(clause):
        return Target(
            kind=TargetKind.GLOBAL, role=role, resolved_by="the person said everywhere"
        ), None
    named = _section_by_name(clause, ctx)
    if named:
        return section_target(named, "named section"), None
    if _FIRST.search(clause) and sections:
        return section_target(sections[0].section_id, "the first section"), None
    if _LAST.search(clause) and sections and intent != "abrupt_ending":
        return section_target(sections[-1].section_id, "the last section"), None
    if _LAST.search(clause) and intent == "abrupt_ending" and sections:
        return section_target(sections[-1].section_id, "the last section"), None
    if _PREV_CLIP.search(clause):
        primary = [
            c for c in timeline.primary() if anchor is not None and c.end_frame <= anchor + 1
        ]
        if primary:
            return Target(
                kind=TargetKind.CLIPS,
                clip_ids=(primary[-1].clip_id,),
                resolved_by="the shot before the playhead",
            ), None
        return _ask_clip(ctx, "Which shot do you mean? There is none before the playhead.")
    if _THIS_CLIP.search(clause):
        if ctx.selected_clips:
            return Target(
                kind=TargetKind.CLIPS, clip_ids=ctx.selected_clips, resolved_by="the selected clips"
            ), None
        if anchor is not None:
            at = [c for c in timeline.primary() if c.start_frame <= anchor < c.end_frame]
            if at:
                return Target(
                    kind=TargetKind.CLIPS,
                    clip_ids=(at[0].clip_id,),
                    start_frame=at[0].start_frame,
                    end_frame=at[0].end_frame,
                    resolved_by="the shot under the playhead",
                ), None
        return _ask_clip(ctx, "Which shot do you mean? Select it or move the playhead onto it.")
    if _TRANSITION.search(clause) and (intent in {"change_transition", ""} or role == ""):
        return _transition_target(clause, ctx, anchor)
    if _HERE.search(clause) or (anchor is not None and not role and needs_place):
        section = timeline.section_at(anchor) if anchor is not None else None
        if section is None and ctx.section_id:
            return section_target(ctx.section_id, "the section the person was in"), None
        if section is not None:
            return section_target(section.section_id, "the section under the playhead"), None
        if anchor is None:
            return _ask_section(ctx, "Which section do you mean?")
    if role and anchor is not None and not needs_place:
        section = timeline.section_at(anchor)
        if section is not None and _HERE.search(clause):
            return section_target(section.section_id, "the section under the playhead"), None
    if role:
        return Target(kind=TargetKind.ROLE, role=role, resolved_by=f"all {role}"), None
    if needs_place:
        if ctx.section_id:
            return section_target(ctx.section_id, "the section the person was in"), None
        if len(sections) == 1:
            return section_target(sections[0].section_id, "the only section"), None
        return _ask_section(ctx, "Which part of the video do you mean?")
    return Target(kind=TargetKind.NONE, resolved_by="no target named"), None


def _ask_section(ctx: Context, question: str) -> tuple[Target, Clarification]:
    options = tuple(
        (
            f"{s.name} ({s.start_frame / float(ctx.timeline.fps):.0f}-{s.end_frame / float(ctx.timeline.fps):.0f} s)",
            Target(
                kind=TargetKind.SECTION,
                section_ids=(s.section_id,),
                start_frame=s.start_frame,
                end_frame=s.end_frame,
                resolved_by="chosen by the person",
            ),
        )
        for s in ctx.timeline.sections
    )
    return Target(
        kind=TargetKind.AMBIGUOUS, resolved_by="more than one possibility"
    ), Clarification(question=question, options=options)


def _ask_sound(ctx: Context, intent: str) -> tuple[Target, Clarification]:
    options = tuple(
        (name, Target(kind=TargetKind.ROLE, role=role, resolved_by="chosen by the person"))
        for name, role in (
            ("the music", "music"),
            ("the dialogue", "dialogue"),
            ("the sound effects", "sfx"),
        )
    )
    word = "quieter" if intent == "quieter" else "louder"
    return Target(kind=TargetKind.AMBIGUOUS, resolved_by="no sound named"), Clarification(
        question=f"Which sound should be {word}?", options=options
    )


def _ask_clip(ctx: Context, question: str) -> tuple[Target, Clarification]:
    options = tuple(
        (
            f"{c.name} ({c.start_frame / float(ctx.timeline.fps):.0f}-{c.end_frame / float(ctx.timeline.fps):.0f} s)",
            Target(
                kind=TargetKind.CLIPS,
                clip_ids=(c.clip_id,),
                start_frame=c.start_frame,
                end_frame=c.end_frame,
                resolved_by="chosen by the person",
            ),
        )
        for c in ctx.timeline.primary()[:8]
    )
    return Target(
        kind=TargetKind.AMBIGUOUS, resolved_by="more than one possibility"
    ), Clarification(question=question, options=options)


def _transition_target(
    clause: str, ctx: Context, anchor: int | None
) -> tuple[Target, Clarification | None]:
    transitions = sorted(ctx.timeline.transitions, key=lambda t: t.cut_frame)
    if not transitions:
        return Target(kind=TargetKind.NONE, resolved_by="the timeline has no transitions"), None
    if re.search(r"\b(last|previous)\b", clause, _I):
        pool = [t for t in transitions if anchor is None or t.cut_frame <= anchor] or transitions
        chosen = pool[-1]
        return Target(
            kind=TargetKind.TRANSITION,
            transition_ids=(chosen.transition_id,),
            start_frame=chosen.cut_frame,
            end_frame=chosen.cut_frame,
            resolved_by="the last transition before the playhead",
        ), None
    if anchor is not None:
        nearest = min(transitions, key=lambda t: abs(t.cut_frame - anchor))
        if abs(nearest.cut_frame - anchor) <= 3 * float(ctx.timeline.fps):
            return Target(
                kind=TargetKind.TRANSITION,
                transition_ids=(nearest.transition_id,),
                start_frame=nearest.cut_frame,
                end_frame=nearest.cut_frame,
                resolved_by="the transition nearest the playhead",
            ), None
    if len(transitions) == 1:
        only = transitions[0]
        return Target(
            kind=TargetKind.TRANSITION,
            transition_ids=(only.transition_id,),
            start_frame=only.cut_frame,
            end_frame=only.cut_frame,
            resolved_by="the only transition",
        ), None
    options = tuple(
        (
            f"the cut at {t.cut_frame / float(ctx.timeline.fps):.1f} s ({t.mode})",
            Target(
                kind=TargetKind.TRANSITION,
                transition_ids=(t.transition_id,),
                start_frame=t.cut_frame,
                end_frame=t.cut_frame,
                resolved_by="chosen by the person",
            ),
        )
        for t in transitions
    )
    return Target(kind=TargetKind.AMBIGUOUS, resolved_by="more than one transition"), Clarification(
        question="Which transition do you mean?", options=options
    )


def _params(clause: str, intent: str) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    if intent in {"quieter", "louder"}:
        size = 1.5 if _AMOUNT_SMALL.search(clause) else 6.0 if _AMOUNT_LARGE.search(clause) else 3.0
        found.append(("amount_db", f"{-size if intent == 'quieter' else size:.1f}"))
    seconds = _SECONDS.search(clause)
    if seconds and intent in {"shorten", "lengthen", "music_later"}:
        found.append(("seconds", seconds.group("n")))
    if intent == "more_of":
        what = _MORE_OF.search(clause)
        if what:
            found.append(("what", what.group("what").strip()))
    return tuple(found)


def interpret(
    text: str, author: str, ctx: Context, *, feedback_id: str = ""
) -> tuple[Instruction, ...]:
    """The instructions in a piece of feedback, each with the words it came from."""
    result: list[Instruction] = []
    for number, clause in enumerate(_clauses(text), start=1):
        kind = _kind(clause)
        intent = _intent(clause)
        target, ask = resolve_target(clause, intent, ctx)
        if kind in (InstructionKind.LOCK, InstructionKind.KEEP) and target.kind is TargetKind.NONE:
            target, ask = resolve_target(clause, "remove", ctx)  # a lock needs a place
        if intent == "more_of" and target.kind is TargetKind.NONE:
            target = Target(kind=TargetKind.GLOBAL, resolved_by="a general direction")
        if intent in {"quieter", "louder"} and not target.role and ask is None:
            target, ask = _ask_sound(ctx, intent)
        if kind in (InstructionKind.QUESTION, InstructionKind.APPROVAL, InstructionKind.REJECTION):
            ask = None
        if kind is InstructionKind.CLARIFICATION and not intent:
            ask = Clarification(
                question="I could not tell what to change. What would you like done, and where?"
            )
        confidence = (
            0.4 if ask else 0.6 if target.kind in (TargetKind.ROLE, TargetKind.GLOBAL) else 0.9
        )
        if kind is InstructionKind.PREFERENCE:
            confidence = min(confidence, 0.7)
        params = _params(clause, intent)
        instruction = Instruction(
            instruction_id="i_" + json_codec.digest([feedback_id, text, number])[:12],
            original=text if len(_clauses(text)) == 1 else clause,
            author=author,
            kind=kind,
            intent=intent,
            target=target,
            params=params,
            confidence=confidence,
            clarification=ask,
            feedback_id=feedback_id,
            interpretation=_sentence(kind, intent, target, params, ctx),
        )
        result.append(instruction)
    return tuple(result)


def answer(instruction: Instruction, chosen: Target) -> Instruction:
    """The person's answer to a clarification: the same instruction, now with its target."""
    return replace(
        instruction,
        target=chosen,
        clarification=None,
        confidence=0.9,
        interpretation=instruction.interpretation + f" ({chosen.resolved_by}).",
    )


def _section_name(ctx: Context, section_id: str) -> str:
    section = ctx.timeline.section(section_id)
    return section.name if section else section_id


def _sentence(
    kind: InstructionKind,
    intent: str,
    target: Target,
    params: tuple[tuple[str, str], ...],
    ctx: Context,
) -> str:
    where = {
        TargetKind.SECTION: "section "
        + ", ".join(_section_name(ctx, s) for s in target.section_ids),
        TargetKind.CLIPS: "clip(s) " + ", ".join(target.clip_ids),
        TargetKind.ROLE: f"all {target.role}",
        TargetKind.TRANSITION: "the transition at frame " + str(target.start_frame),
        TargetKind.GLOBAL: "the whole video",
        TargetKind.NONE: "(no place named)",
        TargetKind.AMBIGUOUS: "(not clear which)",
    }[target.kind]
    detail = ", ".join(f"{k}={v}" for k, v in params)
    base = {
        InstructionKind.LOCK: "Leave exactly as it is",
        InstructionKind.KEEP: "Keep",
        InstructionKind.QUESTION: "A question, no change",
        InstructionKind.APPROVAL: "An approval, no change",
        InstructionKind.REJECTION: "A rejection, no change",
        InstructionKind.CLARIFICATION: "Not clear what to change",
    }.get(kind, intent.replace("_", " ").capitalize() if intent else "Not clear what to change")
    prefix = "Would like (a preference): " if kind is InstructionKind.PREFERENCE else ""
    return f"{prefix}{base}: {where}" + (f" ({detail})" if detail else "")


# --- from an instruction to a change ---------------------------------------------------------------

_HANDOFF_OWNER = {
    "shorten": "pacing_intelligence",
    "lengthen": "pacing_intelligence",
    "more_dynamic": "pacing_intelligence",
    "more_of": "broll_intelligence",
    "change_transition": "transition_engine",
    "abrupt_ending": "transition_engine",
    "music_later": "music_and_sound_design",
    "original_audio": "audio_mix_engine",
    "less_processed": "audio_improvement",
}
_STYLE_OWNER = {"captions": "caption_engine", "graphics": "motion_graphics_engine"}
_BUS_OF = {"music": "music", "dialogue": "dialogue", "sfx": "sfx"}


def drafts_of(instruction: Instruction, timeline: TimelineView, mix: MixView) -> tuple[Draft, ...]:
    """The changes an instruction asks for. A question, an approval, a rejection, a lock or a
    keep asks for no change (the application acts on a lock; nothing else here does)."""
    del mix
    if instruction.kind not in (InstructionKind.DIRECTIVE, InstructionKind.PREFERENCE):
        return ()
    if instruction.clarification is not None or instruction.target.kind in (
        TargetKind.AMBIGUOUS,
        TargetKind.NONE,
    ):
        return ()
    target, intent = instruction.target, instruction.intent
    words = f'You said: "{instruction.original}". Understood as: {instruction.interpretation}.'
    ids = (instruction.instruction_id,)
    if intent in {"quieter", "louder"}:
        bus = _BUS_OF.get(target.role, "")
        if not bus:
            return ()
        delta = max(
            -MAX_STEP_DB, min(MAX_STEP_DB, float(dict(instruction.params).get("amount_db", "0")))
        )
        if target.kind in (TargetKind.ROLE, TargetKind.GLOBAL):
            return (
                Draft(
                    kind=ChangeKind.MIX_BUS_GAIN,
                    owner="audio_mix_engine",
                    operation="local_creative_revision",
                    title=f"Change the {bus} level by {delta:+.1f} dB",
                    description=f"{words} Change the whole {bus} bus by {delta:+.1f} dB.",
                    params=(("bus", bus), ("delta_db", f"{delta:.1f}")),
                    expected=f"The {bus} sits {abs(delta):.1f} dB {'lower' if delta < 0 else 'higher'}.",
                    side_effects=(f"Only the {bus} stem and the final mix are rendered again.",),
                    bus=bus,
                    instruction_ids=ids,
                    section_ids=target.section_ids,
                ),
            )
        sounds = [
            s
            for s in timeline.sounds
            if s.role == bus
            and any(
                (sec := timeline.section(sid)) is not None
                and s.start < timeline.fps_seconds(sec.end_frame)
                and s.end > timeline.fps_seconds(sec.start_frame)
                for sid in target.section_ids
            )
        ]
        return tuple(
            Draft(
                kind=ChangeKind.MIX_CLIP_GAIN,
                owner="audio_mix_engine",
                operation="local_creative_revision",
                title=f"Change the level of {bus} clip {s.clip_id} by {delta:+.1f} dB",
                description=f"{words} Change {bus} clip {s.clip_id} by {delta:+.1f} dB.",
                clip_ids=(s.clip_id,),
                params=(("clip", s.clip_id), ("delta_db", f"{delta:.1f}")),
                expected=f"The {bus} is {abs(delta):.1f} dB {'lower' if delta < 0 else 'higher'} here.",
                side_effects=(f"Only the {bus} stem and the final mix are rendered again.",),
                bus=bus,
                instruction_ids=ids,
                section_ids=target.section_ids,
            )
            for s in sounds[:6]
        )
    if intent == "remove" and target.kind is TargetKind.CLIPS:
        drafts: list[Draft] = []
        for clip_id in target.clip_ids:
            clip = timeline.clip(clip_id)
            important = clip is None or clip.role == "primary"
            drafts.append(
                Draft(
                    kind=ChangeKind.DELETE_CLIP,
                    owner="timeline_builder",
                    operation="local_creative_revision",
                    title=f"Remove clip '{clip.name if clip else clip_id}'",
                    description=f"{words} Delete the clip from the timeline (the footage stays in the library).",
                    clip_ids=(clip_id,),
                    params=(("clip", clip_id),),
                    expected="The clip is gone from the timeline.",
                    side_effects=("Nothing closes the gap it leaves on a main track.",)
                    if important
                    else (),
                    minimum_level=Level.MANUAL_ONLY if important else Level.SUGGESTED,
                    instruction_ids=ids,
                    section_ids=tuple(
                        s for s in ([clip.section_id] if clip and clip.section_id else [])
                    ),
                )
            )
        return tuple(drafts)
    if intent in {"softer_style"}:
        owner = _STYLE_OWNER.get(target.role, "visual_style_engine")
    else:
        owner = _HANDOFF_OWNER.get(intent, "")
    if not owner:
        return ()
    return (
        Draft(
            kind=ChangeKind.HANDOFF,
            owner=owner,
            operation="local_creative_revision",
            title=f"{intent.replace('_', ' ').capitalize()}: ask {owner}",
            description=f"{words} Ask {owner} to {intent.replace('_', ' ')}.",
            clip_ids=target.clip_ids,
            track_ids=(),
            params=(("intent", intent), *instruction.params, ("target", target.kind.value)),
            expected="The next report and your own review show the change.",
            minimum_level=Level.MANUAL_ONLY if intent == "remove" else Level.SUGGESTED,
            instruction_ids=ids,
            section_ids=target.section_ids,
        ),
    )
