"""The detectors: one question each, from the evidence at the two sides of one cut.

Each detector returns a ``Finding`` (a problem, with its scores, confidence and evidence), a
``Missing`` (the evidence cannot decide, and what is absent) or ``None`` (no problem found, or it
does not apply). A value that was not measured is skipped, never read as "matches" or "differs".
Every threshold is an uncalibrated design choice, named here once. Scores are heuristic rankings,
not probabilities.

* ``jump_cut``: same picture, the subject has visibly moved.
* ``visual_discontinuity``: the two pictures look unrelated (embedding, environment, shot size).
* ``lighting``: the largest step in exposure, colour balance or lighting set-up.
* ``spatial``: travel direction reversed; eyeline flipped or axis possibly crossed.
* ``action``: the subject is not where continued motion would put it; objects appeared or vanished.
* ``identity``: another person (appearance clusters) or another product model (text on screen).
* ``audio``: a word cut through, a breath cut, a step in level or noise floor, unverified sync.
* ``temporal``: the cut runs backwards through one recording without a declared reorder.
* ``narrative``: a hard visual break across story sections with nothing to bridge it.
"""

from dataclasses import dataclass
from fractions import Fraction
from math import hypot, sqrt

from media_house.modules.continuity_intelligence.domain.facts import (
    AudioEdge,
    EdgeFact,
    SideFact,
    SubjectFact,
    TransitionFact,
)
from media_house.modules.continuity_intelligence.domain.model import EvidenceRef, Score
from media_house.modules.continuity_intelligence.domain.values import (
    IdentityVerdict,
    IssueType,
    Scope,
    ScoreBasis,
    ScoreName,
    TransitionKind,
)

# --- thresholds (design choices) -------------------------------------------------------------
SAME_FRAME_SIMILARITY = 0.88  # embedding cosine from which two shots count as the same picture
NOTICEABLE_CHANGE = (0.02, 0.12)  # picture units of subject change: invisible .. clearly different
SIMILARITY_RANGE = (0.30, 0.90)  # embedding cosine: unrelated .. the same picture
LUMA_STEP, BALANCE_STEP, SURROUND_STEP = 0.25, 0.35, 0.5
TRAVEL_MIN = 0.05  # picture units per second that count as travelling
EYELINE_MIN_YAW = 15.0  # degrees
ACTION_ERROR = (0.10, 0.40)
ACTION_MAX_GAP = 3.0
LEVEL_STEP = (3.0, 12.0)  # dB between the two sides' levels
FLOOR_STEP = (3.0, 10.0)
WORD_EDGE_SAFE = 0.12  # seconds from a word's edge from which a cut is no longer "inside" it
REVERSAL_RANGE = (1.0, 30.0)  # seconds a cut runs backwards through a recording
_SIZE = {"wide": 0, "medium": 1, "close_up": 2, "extreme_close_up": 3}


@dataclass(frozen=True, slots=True)
class Finding:
    type: IssueType
    scope: Scope
    scores: tuple[Score, ...]
    #: The strongest problem strength of this finding, in [0, 1], risk direction.
    peak: float
    confidence: float | None
    explanation: str
    why: str
    evidence: tuple[EvidenceRef, ...]
    questions: tuple[str, ...] = ()
    identity: IdentityVerdict | None = None
    #: Extra context that makes the difference likelier to be deliberate (named reason).
    style_hints: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class Missing:
    question: str
    missing: tuple[str, ...]


@dataclass(slots=True)
class Detected:
    findings: list[Finding]
    missing: list[Missing]
    #: What was compared, and the largest difference found (for the "compatible pair" record).
    compared: list[str]
    differences: list[float]


def smooth(value: float, low: float, high: float) -> float:
    return max(0.0, min(1.0, (value - low) / (high - low)))


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float | None:
    if not a or len(a) != len(b):
        return None
    na, nb = sqrt(sum(x * x for x in a)), sqrt(sum(x * x for x in b))
    return None if na == 0 or nb == 0 else sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


def _score(name: ScoreName, value: float | None, basis: ScoreBasis, reason: str) -> Score:
    return Score(
        name=name,
        value=None if value is None else round(max(0.0, min(1.0, value)), 3),
        basis=basis,
        reason=reason,
    )


def _ref(kind: str, side: SideFact, detail: str) -> EvidenceRef:
    return EvidenceRef(
        kind=kind, ref=side.shot_id or side.clip_id, asset_id=side.asset_id, detail=detail
    )


def _similarity(a: EdgeFact | None, b: EdgeFact | None) -> float | None:
    if a is None or b is None or a.embedding_model != b.embedding_model:
        return None
    return cosine(a.embedding, b.embedding)


def _same_scene(t: TransitionFact) -> bool | None:
    if t.kind is TransitionKind.CROSS_RECORDING:
        return False
    a, b = t.outgoing, t.incoming
    if t.kind is TransitionKind.SAME_SHOT or (a.shot_id is not None and a.shot_id == b.shot_id):
        return True
    if a.scene_id is None or b.scene_id is None:
        return None
    return a.scene_id == b.scene_id


def _same_frame(t: TransitionFact, similarity: float | None) -> bool | None:
    if t.kind is TransitionKind.SAME_SHOT:
        return True
    if t.kind is TransitionKind.CROSS_RECORDING:
        return False
    if similarity is None:
        return None
    return similarity >= SAME_FRAME_SIMILARITY and _same_scene(t) is not False


def detect(t: TransitionFact) -> Detected:
    out = Detected([], [], [], [])
    a, b = t.outgoing.edge, t.incoming.edge
    similarity = _similarity(a, b)
    _jump_cut(t, a, b, similarity, out)
    _visual(t, a, b, similarity, out)
    _lighting(t, a, b, out)
    _spatial(t, a, b, out)
    _action(t, a, b, out)
    _identity(t, a, b, out)
    _audio(t, out)
    _temporal(t, out)
    _narrative(t, out)
    return out


# --- jump cut ----------------------------------------------------------------------------------
def _subjects(a: EdgeFact | None, b: EdgeFact | None) -> tuple[SubjectFact, SubjectFact] | None:
    if a is None or b is None or a.subject is None or b.subject is None:
        return None
    return a.subject, b.subject


def _jump_cut(
    t: TransitionFact, a: EdgeFact | None, b: EdgeFact | None, sim: float | None, out: Detected
) -> None:
    same = _same_frame(t, sim)
    if t.covered or same is not True:
        return
    pair = _subjects(a, b)
    if pair is None:
        out.missing.append(
            Missing(
                "does the subject visibly jump at this cut?",
                ("the subject was not tracked on both sides of the cut",),
            )
        )
        return
    s1, s2 = pair
    change = hypot(s2.cx - s1.cx, s2.cy - s1.cy) + 0.5 * abs(s2.height - s1.height)
    noticeable = smooth(change, *NOTICEABLE_CHANGE)
    out.compared.append("subject position")
    out.differences.append(noticeable)
    sameness = (
        1.0
        if t.kind is TransitionKind.SAME_SHOT
        else 0.6 + 0.4 * smooth(sim or 0.0, SAME_FRAME_SIMILARITY, 1.0)
    )
    face = 1.0 if (a and b and (a.head_yaw is not None or b.head_yaw is not None)) else 0.7
    short = 0.5 if t.gap is not None and t.gap < Fraction(3, 10) else 1.0
    severity = sameness * noticeable * face * short * (0.75 if t.bridged else 1.0)
    if severity <= 0.0:
        return
    covered = 0.5 + (0.2 if face == 1.0 else 0.0) + (0.15 if sim is not None else 0.0) + 0.15
    hints = (("the edit removed speech here", 0.25),) if t.speech_trimmed else ()
    out.findings.append(
        Finding(
            type=IssueType.JUMP_CUT,
            scope=Scope.TRANSITION,
            scores=(
                _score(
                    ScoreName.JUMP_CUT_SEVERITY,
                    severity,
                    ScoreBasis.HEURISTIC,
                    f"same frame, the subject moved {change:.2f} picture units "
                    f"({t.kind.value.replace('_', ' ')})",
                ),
            ),
            peak=severity,
            confidence=min(1.0, covered),
            explanation=(
                f"the subject jumps {change:.2f} of the picture across a cut inside one continuous "
                "picture"
            ),
            why="the frame stays the same while the subject pops to a new pose: viewers notice it "
            "as a cut in time",
            evidence=(
                _ref("track", t.outgoing, f"subject at {s1.cx:.2f},{s1.cy:.2f} h {s1.height:.2f}"),
                _ref("track", t.incoming, f"subject at {s2.cx:.2f},{s2.cy:.2f} h {s2.height:.2f}"),
            ),
            style_hints=hints,
        )
    )


# --- visual discontinuity ----------------------------------------------------------------------
def _visual(
    t: TransitionFact, a: EdgeFact | None, b: EdgeFact | None, sim: float | None, out: Detected
) -> None:
    if a is None or b is None:
        out.missing.append(
            Missing("do the two pictures belong together?", ("no picture evidence on a side",))
        )
        return
    if _same_frame(t, sim) is True:
        if sim is not None:
            out.compared.append("looks")
            out.differences.append(1.0 - smooth(sim, *SIMILARITY_RANGE))
        return
    parts: list[tuple[str, float, float]] = []
    if sim is not None:
        parts.append(("looks", 1.0 - smooth(sim, *SIMILARITY_RANGE), 0.5))
    if a.environment and b.environment:
        parts.append(("environment", 0.0 if a.environment == b.environment else 1.0, 0.3))
    if a.indoor_outdoor and b.indoor_outdoor:
        parts.append(("indoor/outdoor", 0.0 if a.indoor_outdoor == b.indoor_outdoor else 0.8, 0.2))
    if a.framing in _SIZE and b.framing in _SIZE:
        jump = abs(_SIZE[a.framing] - _SIZE[b.framing])
        parts.append(("shot size", 0.0 if jump == 0 else 0.15 if jump == 1 else 0.5, 0.15))
    if not parts:
        out.missing.append(
            Missing(
                "do the two pictures belong together?",
                ("no embedding, environment or framing on both sides",),
            )
        )
        return
    total = sum(w for _, _, w in parts)
    score = sum(v * w for _, v, w in parts) / total
    out.compared += [n for n, _, _ in parts]
    out.differences += [v for _, v, _ in parts]
    if score <= 0.0:
        return
    detail = ", ".join(f"{n} {v:.2f}" for n, v, _ in parts)
    out.findings.append(
        Finding(
            type=IssueType.VISUAL_DISCONTINUITY,
            scope=Scope.TRANSITION,
            scores=(
                _score(
                    ScoreName.VISUAL_DISCONTINUITY,
                    score,
                    ScoreBasis.HEURISTIC,
                    f"weighted mean of: {detail}",
                ),
            ),
            peak=score,
            confidence=min(1.0, total / 1.15),
            explanation=f"the pictures differ ({detail})",
            why="two shots that look unrelated can make the viewer lose where or when they are",
            evidence=(
                _ref("embedding" if sim is not None else "video_edge", t.outgoing, detail),
                _ref("embedding" if sim is not None else "video_edge", t.incoming, detail),
            ),
        )
    )


# --- lighting ----------------------------------------------------------------------------------
def _lighting(t: TransitionFact, a: EdgeFact | None, b: EdgeFact | None, out: Detected) -> None:
    if a is None or b is None:
        return
    parts: list[tuple[str, float, str]] = []
    if a.luma is not None and b.luma is not None:
        d = abs(a.luma - b.luma)
        parts.append(("exposure", d / LUMA_STEP, f"median luma {a.luma:.2f} -> {b.luma:.2f}"))
    if a.color_balance is not None and b.color_balance is not None:
        d = abs(a.color_balance - b.color_balance)
        parts.append(
            (
                "colour balance",
                d / BALANCE_STEP,
                f"red/blue {a.color_balance:.2f} -> {b.color_balance:.2f}",
            )
        )
    if a.subject_to_surround is not None and b.subject_to_surround is not None:
        d = abs(a.subject_to_surround - b.subject_to_surround)
        parts.append(("lighting set-up", d / SURROUND_STEP, "subject against surround changes"))
    if a.backlit is not None and b.backlit is not None and a.backlit != b.backlit:
        parts.append(("backlight", 0.8, "one side is backlit"))
    if not parts:
        return
    out.compared += [n for n, _, _ in parts]
    out.differences += [min(1.0, v) for _, v, _ in parts]
    name, value, detail = max(parts, key=lambda p: p[1])
    score = min(1.0, value)
    if score < 0.15:
        return
    out.findings.append(
        Finding(
            type=IssueType.LIGHTING_MISMATCH,
            scope=Scope.TRANSITION,
            scores=(
                _score(
                    ScoreName.LIGHTING_MISMATCH,
                    score,
                    ScoreBasis.DERIVED,
                    f"largest step: {name} ({detail})",
                ),
            ),
            peak=score,
            confidence=min(1.0, 0.45 + 0.2 * len(parts)),
            explanation=f"{name} changes across the cut ({detail})",
            why="a step in exposure or colour between neighbouring shots reads as a change of "
            "time or place, or as a technical flaw",
            evidence=(
                _ref("lighting", t.outgoing, detail),
                _ref("lighting", t.incoming, detail),
            ),
        )
    )


# --- spatial -----------------------------------------------------------------------------------
def _spatial(t: TransitionFact, a: EdgeFact | None, b: EdgeFact | None, out: Detected) -> None:
    if a is None or b is None:
        return
    scene = _same_scene(t)
    pair = _subjects(a, b)
    if pair is not None:
        s1, s2 = pair
        if (
            s1.vx is not None
            and s2.vx is not None
            and abs(s1.vx) >= TRAVEL_MIN
            and abs(s2.vx) >= TRAVEL_MIN
        ):
            if scene is not True:
                out.missing.append(
                    Missing(
                        "does the subject keep travelling in the same direction?",
                        ("the two shots are not known to share a viewpoint",),
                    )
                )
            elif (s1.vx > 0) != (s2.vx > 0):
                score = 1.0 - min(1.0, (abs(s1.vx) + abs(s2.vx)) / 0.5)
                out.compared.append("travel direction")
                out.differences.append(1.0 - score)
                out.findings.append(
                    Finding(
                        type=IssueType.SCREEN_DIRECTION,
                        scope=Scope.TRANSITION,
                        scores=(
                            _score(
                                ScoreName.SPATIAL_CONTINUITY,
                                score,
                                ScoreBasis.HEURISTIC,
                                f"travel {s1.vx:+.2f} -> {s2.vx:+.2f} picture units per second",
                            ),
                        ),
                        peak=1.0 - score,
                        confidence=0.7 if t.kind is TransitionKind.SAME_SHOT else 0.55,
                        explanation="the subject travels one way, then the other way after the cut",
                        why="a reversal of screen direction makes the subject seem to turn back",
                        evidence=(
                            _ref("track", t.outgoing, f"vx {s1.vx:+.2f}"),
                            _ref("track", t.incoming, f"vx {s2.vx:+.2f}"),
                        ),
                    )
                )
            else:
                out.compared.append("travel direction")
                out.differences.append(0.0)
    _eyeline(t, a, b, pair, scene, out)


def _eyeline(
    t: TransitionFact,
    a: EdgeFact,
    b: EdgeFact,
    pair: tuple[SubjectFact, SubjectFact] | None,
    scene: bool | None,
    out: Detected,
) -> None:
    if a.head_yaw is None or b.head_yaw is None:
        return
    if abs(a.head_yaw) < EYELINE_MIN_YAW or abs(b.head_yaw) < EYELINE_MIN_YAW:
        return
    flipped = (a.head_yaw > 0) != (b.head_yaw > 0)
    magnitude = smooth(abs(a.head_yaw) + abs(b.head_yaw), 30.0, 90.0)
    same_person = (
        pair is not None
        and pair[0].identity_cluster is not None
        and pair[0].identity_cluster == pair[1].identity_cluster
    )
    other_person = (
        pair is not None
        and pair[0].identity_cluster is not None
        and pair[1].identity_cluster is not None
        and pair[0].identity_cluster != pair[1].identity_cluster
    )
    if same_person and flipped and scene is True:
        reason, explanation = (
            "same person looks the other way",
            ("the same person looks to one side, then to the other after the cut"),
        )
    elif other_person and not flipped:
        speaking = (a.visual_speaking or 0.0) >= 0.3 or (b.visual_speaking or 0.0) >= 0.3
        if not speaking:
            return
        reason, explanation = (
            "two people look the same way",
            ("two people in conversation look the same way: the axis may have been crossed"),
        )
    else:
        return
    out.compared.append("eyeline")
    out.differences.append(magnitude)
    out.findings.append(
        Finding(
            type=IssueType.EYELINE,
            scope=Scope.TRANSITION,
            scores=(
                _score(
                    ScoreName.SPATIAL_CONTINUITY,
                    1.0 - magnitude,
                    ScoreBasis.HEURISTIC,
                    f"{reason}: head yaw {a.head_yaw:+.0f} -> {b.head_yaw:+.0f} degrees",
                ),
            ),
            peak=magnitude,
            confidence=0.5,
            explanation=explanation,
            why="viewers read eyelines as the geometry of the scene; a flip makes it hard to place "
            "the people",
            evidence=(
                _ref("face", t.outgoing, f"yaw {a.head_yaw:+.0f}"),
                _ref("face", t.incoming, f"yaw {b.head_yaw:+.0f}"),
            ),
            questions=("is the sign of the measured head yaw the same direction in both shots?",),
        )
    )


# --- action and objects ------------------------------------------------------------------------
def _action(t: TransitionFact, a: EdgeFact | None, b: EdgeFact | None, out: Detected) -> None:
    if a is None or b is None or t.kind is TransitionKind.CROSS_RECORDING or t.covered:
        return
    pair = _subjects(a, b)
    if pair is not None and t.gap is not None and 0 < t.gap <= Fraction(str(ACTION_MAX_GAP)):
        s1, s2 = pair
        if s1.vx is not None and s1.vy is not None and hypot(s1.vx, s1.vy) >= TRAVEL_MIN:
            seconds = float(t.gap)
            px = s1.cx + max(-0.5, min(0.5, s1.vx * seconds))
            py = s1.cy + max(-0.5, min(0.5, s1.vy * seconds))
            error = hypot(s2.cx - px, s2.cy - py)
            score = 1.0 - smooth(error, *ACTION_ERROR)
            out.compared.append("action phase")
            out.differences.append(1.0 - score)
            if score <= 0.6:
                out.findings.append(
                    Finding(
                        type=IssueType.ACTION_CONTINUITY,
                        scope=Scope.TRANSITION,
                        scores=(
                            _score(
                                ScoreName.ACTION_CONTINUITY,
                                score,
                                ScoreBasis.HEURISTIC,
                                f"continued motion would put the subject {error:.2f} picture "
                                f"units from where it is after {seconds:.1f} s skipped",
                            ),
                        ),
                        peak=1.0 - score,
                        confidence=0.45,
                        explanation=(
                            f"the subject was moving, {seconds:.1f} s are skipped, "
                            "and it is not where the motion would carry it"
                        ),
                        why="a skipped part of an action can read as a missing step or a jump in "
                        "time",
                        evidence=(
                            _ref("track", t.outgoing, f"v ({s1.vx:+.2f}, {s1.vy:+.2f})"),
                            _ref("track", t.incoming, f"at ({s2.cx:.2f}, {s2.cy:.2f})"),
                        ),
                        questions=("is the skipped part an essential step of the action?",),
                    )
                )
    if _same_scene(t) is True and a.objects and b.objects:
        gone, new = set(a.objects) - set(b.objects), set(b.objects) - set(a.objects)
        union = set(a.objects) | set(b.objects)
        change = len(gone | new) / len(union)
        out.compared.append("visible objects")
        out.differences.append(change)
        if change >= 0.5 and (gone or new):
            score = smooth(change, 0.3, 0.8)
            out.findings.append(
                Finding(
                    type=IssueType.OBJECT_STATE,
                    scope=Scope.TRANSITION,
                    scores=(
                        _score(
                            ScoreName.VISUAL_DISCONTINUITY,
                            score,
                            ScoreBasis.HEURISTIC,
                            f"objects gone: {sorted(gone)}, new: {sorted(new)}",
                        ),
                    ),
                    peak=score * 0.8,
                    confidence=0.35,
                    explanation=f"objects change: gone {sorted(gone)}, new {sorted(new)}",
                    why="an object that appears or disappears can mean an edit skipped a step",
                    evidence=(
                        _ref("video_edge", t.outgoing, ", ".join(sorted(a.objects))),
                        _ref("video_edge", t.incoming, ", ".join(sorted(b.objects))),
                    ),
                    questions=("are the changed objects only outside the new framing?",),
                )
            )


# --- identity ----------------------------------------------------------------------------------
def _identity(t: TransitionFact, a: EdgeFact | None, b: EdgeFact | None, out: Detected) -> None:
    if a is None or b is None:
        return
    for x in a.names:
        for y in b.names:
            if x.family and x.family == y.family and x.value != y.value:
                out.compared.append("product name")
                out.differences.append(0.9)
                out.findings.append(
                    Finding(
                        type=IssueType.IDENTITY_MISMATCH,
                        scope=Scope.TRANSITION,
                        scores=(
                            _score(
                                ScoreName.IDENTITY_MISMATCH_RISK,
                                0.9,
                                ScoreBasis.DERIVED,
                                f"text on screen names {x.text} and {y.text}",
                            ),
                        ),
                        peak=0.9,
                        confidence=0.8,
                        explanation=f"the picture names {x.text} before the cut, {y.text} after it",
                        why="another model of the product after the cut breaks the demonstration",
                        evidence=(
                            _ref("ocr_text", t.outgoing, x.text),
                            _ref("ocr_text", t.incoming, y.text),
                        ),
                        identity=IdentityVerdict.CONFIRMED_MISMATCH,
                    )
                )
                return
    pair = _subjects(a, b)
    if pair is None or pair[0].identity_cluster is None or pair[1].identity_cluster is None:
        return
    same = pair[0].identity_cluster == pair[1].identity_cluster
    out.compared.append("person appearance")
    if same:
        out.differences.append(0.0)
        return
    if t.kind is TransitionKind.CROSS_RECORDING and not t.related_assets:
        out.missing.append(
            Missing(
                "is it meant to be the same person?",
                ("the recordings are not known to show the same scene",),
            )
        )
        out.differences.append(0.3)
        return
    out.differences.append(0.65)
    out.findings.append(
        Finding(
            type=IssueType.IDENTITY_MISMATCH,
            scope=Scope.TRANSITION,
            scores=(
                _score(
                    ScoreName.IDENTITY_MISMATCH_RISK,
                    0.65,
                    ScoreBasis.DERIVED,
                    f"appearance clusters {pair[0].identity_cluster} and "
                    f"{pair[1].identity_cluster} "
                    "differ in what looks like one scene",
                ),
            ),
            peak=0.65,
            confidence=0.4,
            explanation="the main person looks different on the two sides of a cut in one scene",
            why="a different-looking person after the cut breaks who the viewer is following",
            evidence=(
                _ref("track", t.outgoing, pair[0].identity_cluster or ""),
                _ref("track", t.incoming, pair[1].identity_cluster or ""),
            ),
            questions=("are these two different people, or one person seen differently?",),
            identity=IdentityVerdict.PROBABLE_MISMATCH,
        )
    )


# --- audio ---------------------------------------------------------------------------------------
def _audio(t: TransitionFact, out: Detected) -> None:
    x, y = t.outgoing.audio, t.incoming.audio
    if t.outgoing.sync in ("offset_indicated", "ambiguous") or t.incoming.sync in (
        "offset_indicated",
        "ambiguous",
    ):
        out.findings.append(
            Finding(
                type=IssueType.SYNC_UNCERTAIN,
                scope=Scope.TRANSITION,
                scores=(),
                peak=0.3,
                confidence=0.4,
                explanation="the alignment cannot confirm that sound and picture are in sync here",
                why="a small drift is invisible in one clip but shows when clips join",
                evidence=(
                    EvidenceRef(
                        kind="alignment",
                        ref=t.outgoing.clip_id,
                        asset_id=t.outgoing.asset_id,
                        detail=t.outgoing.sync,
                    ),
                    EvidenceRef(
                        kind="alignment",
                        ref=t.incoming.clip_id,
                        asset_id=t.incoming.asset_id,
                        detail=t.incoming.sync,
                    ),
                ),
                questions=("is the sync offset the alignment indicated applied?",),
            )
        )
    if x is None and y is None:
        return
    _clipped(t, x, y, out)
    parts: list[tuple[str, float, str]] = []
    if x and y and x.level_db is not None and y.level_db is not None:
        step = abs(x.level_db - y.level_db)
        weight = 1.0 if (x.speech_near and y.speech_near) else 0.8
        parts.append(
            (
                "level",
                smooth(step, *LEVEL_STEP) * weight,
                f"{x.level_db:.0f} -> {y.level_db:.0f} dB",
            )
        )
    if x and y and x.floor_db is not None and y.floor_db is not None:
        step = abs(x.floor_db - y.floor_db)
        parts.append(
            ("noise floor", smooth(step, *FLOOR_STEP), f"{x.floor_db:.0f} -> {y.floor_db:.0f} dB")
        )
    breath = (x is not None and x.inside_breath) or (y is not None and y.inside_breath)
    if breath:
        parts.append(("cut breath", 0.5, "the cut falls inside a breath"))
    if not parts:
        return
    out.compared += [n for n, _, _ in parts]
    out.differences += [v for _, v, _ in parts]
    name, value, detail = max(parts, key=lambda p: p[1])
    score = value * (0.7 if t.bridged else 1.0)
    if score < 0.15:
        return
    out.findings.append(
        Finding(
            type=IssueType.AUDIO_DISCONTINUITY,
            scope=Scope.TRANSITION,
            scores=(
                _score(
                    ScoreName.AUDIO_DISCONTINUITY,
                    score,
                    ScoreBasis.DERIVED,
                    f"largest: {name} ({detail})",
                ),
            ),
            peak=score,
            confidence=min(1.0, 0.4 + 0.25 * len(parts)),
            explanation=f"the sound changes at the cut: {name} {detail}",
            why="an abrupt step in level, room tone or breath is heard as an edit",
            evidence=(
                EvidenceRef(
                    kind="audio_level",
                    ref=t.outgoing.clip_id,
                    asset_id=t.outgoing.asset_id,
                    detail=detail,
                ),
                EvidenceRef(
                    kind="audio_level",
                    ref=t.incoming.clip_id,
                    asset_id=t.incoming.asset_id,
                    detail=detail,
                ),
            ),
        )
    )


def _clipped(t: TransitionFact, x: AudioEdge | None, y: AudioEdge | None, out: Detected) -> None:
    worst: tuple[float, str, SideFact] | None = None
    for side, edge in ((t.outgoing, x), (t.incoming, y)):
        if edge is None or edge.straddled_word is None or edge.word_edge_distance is None:
            continue
        severity = 1.0 - smooth(edge.word_edge_distance, 0.0, WORD_EDGE_SAFE)
        if worst is None or severity > worst[0]:
            worst = (severity, edge.straddled_word, side)
    if worst is None or worst[0] < 0.2:
        return
    severity, word, side = worst
    out.compared.append("cut through a word")
    out.differences.append(severity)
    out.findings.append(
        Finding(
            type=IssueType.CLIPPED_SPEECH,
            scope=Scope.TRANSITION,
            scores=(
                _score(
                    ScoreName.AUDIO_DISCONTINUITY,
                    min(0.95, severity),
                    ScoreBasis.DERIVED,
                    f"the cut falls inside the word '{word}'",
                ),
            ),
            peak=min(0.95, severity),
            confidence=0.8,
            explanation=f"the cut falls inside the word '{word}'",
            why="a word cut off or started late is heard as a mistake",
            evidence=(
                EvidenceRef(
                    kind="audio_word", ref=side.clip_id, asset_id=side.asset_id, detail=word
                ),
            ),
        )
    )


# --- order ---------------------------------------------------------------------------------------
def _temporal(t: TransitionFact, out: Detected) -> None:
    if not t.reordered or t.kind is TransitionKind.CROSS_RECORDING:
        return
    back = float(t.outgoing.moment - t.incoming.moment)
    score = smooth(back, *REVERSAL_RANGE)
    out.compared.append("source order")
    out.differences.append(score)
    if score <= 0.0:
        return
    hints = []
    if t.section_change:
        hints.append(("the cut is a story section boundary", 0.5))
    if t.cold_open:
        hints.append(("the clip is a cold open", 0.7))
    out.findings.append(
        Finding(
            type=IssueType.TEMPORAL_INCONSISTENCY,
            scope=Scope.SEQUENCE,
            scores=(
                _score(
                    ScoreName.TEMPORAL_INCONSISTENCY,
                    score,
                    ScoreBasis.HEURISTIC,
                    f"the incoming range lies {back:.1f} s earlier in the recording",
                ),
            ),
            peak=score,
            confidence=0.5,
            explanation=f"the edit jumps back {back:.1f} s in the recording",
            why="material from earlier in the recording played later can show a past state as the "
            "present",
            evidence=(
                EvidenceRef(
                    kind="edit_plan",
                    ref=t.outgoing.clip_id,
                    asset_id=t.outgoing.asset_id,
                    detail=f"ends {float(t.outgoing.moment):.1f} s",
                ),
                EvidenceRef(
                    kind="edit_plan",
                    ref=t.incoming.clip_id,
                    asset_id=t.incoming.asset_id,
                    detail=f"starts {float(t.incoming.moment):.1f} s",
                ),
            ),
            questions=("is the reorder deliberate (flashback, cold open, story order)?",),
            style_hints=tuple(hints),
        )
    )


def _narrative(t: TransitionFact, out: Detected) -> None:
    if not t.section_change or t.covered or t.bridged or t.kind is TransitionKind.CROSS_RECORDING:
        return
    visual = next((f for f in out.findings if f.type is IssueType.VISUAL_DISCONTINUITY), None)
    if visual is None or visual.peak < 0.5:
        return
    score = visual.peak * 0.8
    out.findings.append(
        Finding(
            type=IssueType.NARRATIVE_CONFUSION,
            scope=Scope.SECTION,
            scores=(
                _score(
                    ScoreName.NARRATIVE_CONFUSION,
                    score,
                    ScoreBasis.HEURISTIC,
                    "a hard visual break at a story section boundary with nothing to bridge it",
                ),
            ),
            peak=score,
            confidence=min(1.0, (visual.confidence or 0.4) * 0.8),
            explanation="the story moves to a new section with a hard visual break and no bridge",
            why="the viewer may not realise the story has moved on",
            evidence=visual.evidence,
            style_hints=(("the cut is a story section boundary", 0.35),),
        )
    )
