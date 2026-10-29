"""A labelled SYNTHETIC evaluation of the framing planner.

Fifteen situations (the ones the module is meant to handle), each with the outcome the arithmetic
must reach, and an independent check of the planner's own claims: every crop is recomputed against
the observed boxes (validity, clipping, enlargement, speed) without the planner's code, and every
plan is repeated under a tiny deterministic perturbation of the tracks (stability).

It guards regressions and exposes drift between what a plan SAYS and what it DOES. The cases were
written with the rules, so a perfect score says nothing about real footage, a real tracker or how a
picture looks: no real footage was evaluated.

Run it: ``uv run python -m tests.support.refr_eval``
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from media_house.modules.reframing_intelligence.domain.elements import select
from media_house.modules.reframing_intelligence.domain.facts import (
    ElementTrack,
    Sample,
    ShotFacts,
    VisualNeed,
)
from media_house.modules.reframing_intelligence.domain.geometry import (
    Box,
    SourceGeometry,
    TargetFormat,
    crop_problems,
    effective_scale,
    widest_crop,
)
from media_house.modules.reframing_intelligence.domain.model import crop_at
from media_house.modules.reframing_intelligence.domain.profile import SafeArea
from media_house.modules.reframing_intelligence.domain.values import Feasibility, Strategy
from media_house.modules.reframing_intelligence.domain.verdict import ShotPlan
from tests.support.refr_fakes import (
    ASSET,
    T11,
    T169,
    T916,
    beat,
    f,
    geom,
    plan,
    profile,
    shot,
    still,
    walk,
)

PERSON = (0.40, 0.15, 0.60, 0.90)
S = Strategy
F = Feasibility
CROPS = {S.STATIC_CROP, S.REPOSITIONED_CROP}


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    shot: ShotFacts
    options: dict[str, object]
    strategies: frozenset[Strategy]
    feasibilities: frozenset[Feasibility]


def _case(
    name: str,
    one: ShotFacts,
    strategies: set[Strategy],
    feasibilities: set[Feasibility],
    **options: object,
) -> Case:
    return Case(name, one, options, frozenset(strategies), frozenset(feasibilities))


def cases() -> list[Case]:
    ok = {F.FEASIBLE, F.TRADEOFFS}
    demo = VisualNeed("v1", "sec1", "demonstration", True, "the laptop", ASSET, f(0), f(4))
    laptop = still("o", (0.45, 0.5, 0.65, 0.8), kind="object", label="laptop")
    far_laptop = still("o", (0.70, 0.5, 0.90, 0.8), kind="object", label="laptop")
    return [
        _case("1 centred talking head 16:9 -> 9:16", shot(tracks=(still("p", PERSON),)), CROPS, ok),
        _case(
            "2 speaker at the source edge",
            shot(tracks=(still("p", (0.02, 0.15, 0.20, 0.90)),)),
            CROPS,
            ok,
        ),
        _case(
            "3 moving speaker",
            shot(
                end=10,
                tracks=(walk("p", (0.10, 0.2, 0.25, 0.9), (0.55, 0.2, 0.70, 0.9), end=10),),
            ),
            {S.DYNAMIC_CROP},
            ok,
        ),
        _case(
            "4 occlusion gap",
            shot(
                end=6,
                tracks=(
                    walk("p", (0.1, 0.2, 0.25, 0.9), (0.55, 0.2, 0.7, 0.9), end=6, skip=(2.0, 3.6)),
                ),
            ),
            {S.DYNAMIC_CROP},
            ok,
        ),
        _case(
            "5 product demo, presenter and product kept",
            shot(tracks=(still("p", (0.1, 0.2, 0.3, 0.9)), laptop)),
            {S.ORIGINAL, *CROPS},
            ok,
            target=T169,
            geometry=geom(3840, 2160),
            needs=(demo,),
        ),
        _case(
            "6 two speakers cannot share the crop",
            shot(tracks=(still("a", (0.05, 0.2, 0.22, 0.9)), still("b", (0.75, 0.2, 0.92, 0.9)))),
            {S.UNREFRAMEABLE},
            {F.IMPOSSIBLE},
        ),
        _case(
            "7 the crop would cut a required object",
            shot(tracks=(still("p", (0.05, 0.2, 0.25, 0.9)), far_laptop)),
            {S.UNREFRAMEABLE},
            {F.IMPOSSIBLE},
            needs=(demo,),
        ),
        _case(
            "8 source too small for the crop",
            shot(tracks=(still("p", PERSON),)),
            CROPS,
            {F.LOW_QUALITY},
            geometry=geom(640, 360),
        ),
        _case(
            "9 already correctly framed",
            shot(tracks=(still("p", PERSON),)),
            {S.ORIGINAL},
            {F.FEASIBLE},
            target=T169,
        ),
        _case(
            "10 caption space",
            shot(tracks=(still("p", (0.4, 0.40, 0.6, 0.55)),)),
            CROPS,
            ok,
            target=T11,
            geometry=geom(1080, 1920),
            safe_areas=(SafeArea("title", 0.05, 0.0, 0.95, 0.25),),
        ),
        _case(
            "13 deliberately still composition",
            shot(tracks=(walk("p", (0.40, 0.2, 0.55, 0.9), (0.41, 0.2, 0.56, 0.9)),)),
            CROPS,
            ok,
        ),
        _case(
            "14 portrait source to 16:9",
            shot(tracks=(still("p", (0.3, 0.30, 0.7, 0.45)),)),
            CROPS,
            ok,
            target=T169,
            geometry=geom(1080, 1920),
        ),
        _case("15 no tracking at all", shot(), CROPS, {F.UNCERTAIN}),
        _case(
            "15b tracking but no quality measurement",
            shot(usability=None, tracks=(still("p", PERSON),)),
            CROPS,
            {F.UNCERTAIN},
        ),
        _case(
            "16 push-in on a strong beat, footage that can take it",
            shot(end=8, tracks=(still("p", PERSON, end=8),)),
            {S.PUSH_IN},
            ok,
            target=T169,
            geometry=geom(3840, 2160),
            prof=profile("talking_head"),
            beats=(beat("b1", "claim", 0, 8),),
        ),
    ]


@dataclass(frozen=True, slots=True)
class Row:
    name: str
    strategy: str
    feasibility: str
    outcome_ok: bool
    crops_valid: bool
    #: Smallest share of a required element inside the window over its observations (None: no crop).
    min_inside: float | None
    max_scale: float | None
    #: Peak window speed in crop widths per second (None: still or no crop).
    peak_speed: float | None
    #: The plan did not change its strategy or move its first crop more than 2% under a jitter.
    stable: bool
    coverage: float | None


@dataclass(frozen=True, slots=True)
class Report:
    rows: tuple[Row, ...]

    @property
    def agreement(self) -> float:
        return sum(r.outcome_ok for r in self.rows) / len(self.rows)

    @property
    def stability(self) -> float:
        return sum(r.stable for r in self.rows) / len(self.rows)

    @property
    def valid(self) -> float:
        return sum(r.crops_valid for r in self.rows) / len(self.rows)


def _jitter(one: ShotFacts) -> ShotFacts:
    """Every box moved by a tiny deterministic amount (<= 0.002 of the picture)."""
    tracks = []
    for t in one.tracks:
        samples = []
        for i, s in enumerate(t.samples):
            d = 0.002 * (1 if i % 2 else -1)
            b = s.box
            box = Box(
                min(max(b.x0 + d, 0.0), 0.99),
                b.y0,
                min(max(b.x1 + d, 0.01), 1.0),
                b.y1,
            )
            samples.append(Sample(s.t, box, s.confidence))
        tracks.append(ElementTrack(t.track_id, t.kind, t.label, tuple(samples), t.cluster))
    return replace(one, tracks=tuple(tracks))


def _inside_and_speed(
    one: ShotFacts, p: ShotPlan, target: TargetFormat, geometry: SourceGeometry
) -> tuple[float | None, float | None, float | None]:
    if not p.keyframes:
        return None, None, None
    # independent of the planner: the required elements are the primary and what it names
    required = [e.track_id for e in p.elements if e.required]
    worst = 1.0
    for t in one.tracks:
        if t.track_id not in required:
            continue
        for s in t.samples:
            if s.confidence < 0.3:
                continue
            crop = crop_at(p.keyframes, float(s.t))
            width = min(s.box.x1, crop.x1) - max(s.box.x0, crop.x)
            height = min(s.box.y1, crop.y1) - max(s.box.y0, crop.y)
            share = max(width, 0.0) * max(height, 0.0) / s.box.area
            worst = min(worst, share)
    scale = max(effective_scale(k.crop, geometry, target) for k in p.keyframes)
    speed = 0.0
    for a, b in zip(p.keyframes, p.keyframes[1:], strict=False):
        dt = float(b.time.value - a.time.value)
        speed = max(speed, abs(b.crop.center[0] - a.crop.center[0]) / a.crop.w / dt)
    return worst, scale, speed


def evaluate(selected: Callable[[Case], bool] | None = None) -> Report:
    rows = []
    for c in cases():
        if selected is not None and not selected(c):
            continue
        target = c.options.get("target", T916)
        geometry = c.options.get("geometry") or geom()
        assert isinstance(target, TargetFormat) and isinstance(geometry, SourceGeometry)
        p = plan(c.shot, **c.options)  # type: ignore[arg-type]
        again = plan(_jitter(c.shot), **c.options)  # type: ignore[arg-type]
        inside, scale, speed = _inside_and_speed(c.shot, p, target, geometry)
        problems = [x for k in p.keyframes for x in crop_problems(k.crop, geometry, target)]
        first = p.keyframes[0].crop if p.keyframes else None
        other = again.keyframes[0].crop if again.keyframes else None
        stable = p.strategy == again.strategy and (
            first is None
            or other is None
            or max(abs(first.x - other.x), abs(first.y - other.y)) <= 0.02
        )
        w0, _ = widest_crop(geometry, target)
        primary = select(c.shot, (), profile(), (w0, 1.0)).primary
        rows.append(
            Row(
                name=c.name,
                strategy=p.strategy.value,
                feasibility=p.feasibility.value,
                outcome_ok=p.strategy in c.strategies and p.feasibility in c.feasibilities,
                crops_valid=not problems,
                min_inside=inside,
                max_scale=scale,
                peak_speed=speed,
                stable=stable,
                coverage=None if primary is None else round(primary.coverage, 3),
            )
        )
    return Report(tuple(rows))


def format_report(report: Report) -> str:
    lines = [
        f"{'case':<52} {'strategy':<22} {'feasibility':<24} ok valid  inside  scale  speed stable",
    ]
    for r in report.rows:
        inside = "  -  " if r.min_inside is None else f"{r.min_inside:5.2f}"
        scale = "  - " if r.max_scale is None else f"{r.max_scale:4.2f}"
        speed = "  -  " if r.peak_speed is None else f"{r.peak_speed:5.2f}"
        lines.append(
            f"{r.name:<52} {r.strategy:<22} {r.feasibility:<24} "
            f"{'y' if r.outcome_ok else 'N'}  {'y' if r.crops_valid else 'N'}    "
            f"{inside}  {scale}  {speed}  {'y' if r.stable else 'N'}"
        )
    lines.append(
        f"outcome agreement {report.agreement:.2f}  valid crops {report.valid:.2f}  "
        f"stability {report.stability:.2f}   (SYNTHETIC cases written with the rules)"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_report(evaluate()))  # noqa: T201
