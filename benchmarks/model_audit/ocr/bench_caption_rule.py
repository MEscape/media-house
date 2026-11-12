# ruff: noqa: T201, S311, SIM905  (terminal benchmark; seeded RNG for synthetic clips only)
"""Benchmark the burned-in caption rule on stored OCR signals (OCR is not re-run).

For every stored video analysis in a library home it loads the stored ``text`` and ``shots``
signal documents, re-runs only the text-cue derivation (shot starts, ``track_text`` and the
caption branch of ``find_overlays``) and compares caption rules:

* ``legacy``   the rule up to PROCESSING_VERSION 2: > ``caption_min_changes`` distinct strings
               low in a shot -> one caption over every member of the shot;
* ``wordlike`` only word-like strings count, near-duplicates (OCR misreads) are merged;
* ``ordered``  ``wordlike`` plus the strings must replace each other in time;
* ``centred``  ``ordered`` on horizontally centred strings only;
* ``uncrowded`` ``wordlike`` outside screens full of text (shipped in PROCESSING_VERSION 3);
* ``unc+order`` ``uncrowded`` plus time-ordered changes;
* ``shipped``  whatever ``text_cues.find_overlays`` does now (should equal the chosen rule).

Ground truth without hand labels: raw camera / screen / archive files have no burned-in
captions, so every caption span on them is a false positive. Recall comes from a synthetic set
(changing German caption lines with simulated OCR noise; no client text).

Usage::

    uv run python benchmarks/model_audit/ocr/bench_caption_rule.py \
        --home C:/Users/L14/.media_house_scratch/baseline \
        --raw C:/Users/L14/Downloads/yt-video/01_cam C:/Users/L14/Downloads/yt-video/02_scr \
              C:/Users/L14/Downloads/yt-video/04_arc \
        --out C:/Users/L14/.media_house_scratch/benchmarks/ocr

The JSON written to ``--out`` contains recognised text (it can be client text): keep it outside
the repository. Standard output carries only counts.
"""

import argparse
import json
import random
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from media_house.bootstrap.application import Application, StartupOptions
from media_house.core.domain.rational import Rational
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_library.domain.query import MediaQuery
from media_house.modules.media_library.domain.values import MediaType
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.profiles import TextSettings, get_profile
from media_house.modules.video_intelligence.domain.serialization import (
    document_from_json,
    signals_kind,
)
from media_house.modules.video_intelligence.domain.shot_detection import detect_transitions
from media_house.modules.video_intelligence.domain.signals import (
    ShotSignals,
    TextRow,
    TextSignals,
)
from media_house.modules.video_intelligence.domain.text_cues import (
    TrackedText,
    find_overlays,
    track_text,
)
from media_house.modules.video_intelligence.domain.values import AnalyzerId, OverlayKind
from media_house.shared.errors import Ok

type Span = tuple[int, int]  # first, last analysed frame
type Rule = Callable[[list[TrackedText], TextSettings], list[TrackedText] | None]


# --- rules (members of one shot's bottom band -> members that form the caption, or None) -----
def legacy(members: list[TrackedText], settings: TextSettings) -> list[TrackedText] | None:
    return members if len({m.key for m in members}) > settings.caption_min_changes else None


def _letters(key: str) -> int:
    return sum(1 for c in key if c.isalpha())


def _wordlike(key: str, min_letters: int = 3, share: float = 0.5) -> bool:
    chars = key.replace(" ", "")
    return _letters(key) >= min_letters and _letters(key) >= share * len(chars)


#: Strings at least this similar (difflib ratio) count as misreads of one text; > 1 = never.
SAME_RATIO = 0.6


def _same(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= SAME_RATIO


def _clusters(members: Sequence[TrackedText]) -> list[int]:
    """Cluster id per member: a string like any string seen before joins that one's cluster."""
    seen: list[tuple[str, int]] = []
    ids: list[int] = []
    for m in members:
        hit = next((c for k, c in seen if _same(m.key, k)), None)
        if hit is None:
            hit = len({c for _, c in seen})
        seen.append((m.key, hit))
        ids.append(hit)
    return ids


def wordlike(members: list[TrackedText], settings: TextSettings) -> list[TrackedText] | None:
    words = [m for m in members if _wordlike(m.key)]
    return words if len(set(_clusters(words))) > settings.caption_min_changes else None


def ordered(members: list[TrackedText], settings: TextSettings) -> list[TrackedText] | None:
    words = sorted((m for m in members if _wordlike(m.key)), key=lambda m: (m.first, m.last))
    ids = _clusters(words)
    changes = sum(
        1
        for (a, ia), (b, ib) in zip(
            zip(words, ids, strict=True), zip(words[1:], ids[1:], strict=True), strict=False
        )
        if ia != ib and b.first > a.first
    )
    ok = len(set(ids)) > settings.caption_min_changes and changes >= settings.caption_min_changes
    return words if ok else None


def centred(members: list[TrackedText], settings: TextSettings) -> list[TrackedText] | None:
    """``ordered`` on the strings centred horizontally (captions are; UI text mostly is not)."""
    return ordered([m for m in members if abs(m.box.center[0] - 0.5) <= 0.2], settings)


def _crowded(members: list[TrackedText]) -> bool:
    """More than two word-like strings side by side in > 20 % of the read frames (a screen)."""
    frames: dict[int, int] = {}
    for m in members:
        if _wordlike(m.key):
            for f in m.frames:
                frames[f] = frames.get(f, 0) + 1
    return bool(frames) and sum(1 for n in frames.values() if n > 2) > 0.2 * len(frames)


def uncrowded(members: list[TrackedText], settings: TextSettings) -> list[TrackedText] | None:
    """``wordlike`` outside screens full of text (the rule shipped in PROCESSING_VERSION 3)."""
    return None if _crowded(members) else wordlike(members, settings)


def uncrowded_ordered(
    members: list[TrackedText], settings: TextSettings
) -> list[TrackedText] | None:
    """``uncrowded`` plus time-ordered changes (measured: no extra effect, not shipped)."""
    return None if _crowded(members) else ordered(members, settings)


def _legacy_caption_spans(
    items: Sequence[TrackedText], total: int, settings: TextSettings, rule: Rule
) -> list[Span]:
    """The legacy caption branch of ``find_overlays`` with a pluggable decision."""
    remaining = list(items)
    groups: dict[tuple[str, int, int], list[TrackedText]] = {}
    for item in items:
        cx, cy = item.box.center
        groups.setdefault((item.key, round(cx * 20), round(cy * 20)), []).append(item)
    for members in groups.values():
        covered = sum(len(m.frames) for m in members)
        if (
            total
            and covered >= settings.watermark_min_frames
            and covered / total >= (settings.watermark_share)
        ):
            remaining = [i for i in remaining if i not in members]
    by_shot: dict[int, list[TrackedText]] = {}
    for item in remaining:
        if item.box.y0 >= settings.caption_top:
            by_shot.setdefault(item.shot_index, []).append(item)
    spans: list[Span] = []
    for members in by_shot.values():
        used = rule(members, settings)
        if used:
            spans.append((min(m.first for m in used), max(m.last for m in used)))
    return spans


# --- data ------------------------------------------------------------------------------------
@dataclass(slots=True)
class Case:
    name: str
    label: str  # "negative" (no burned-in captions) | "unlabelled" | "positive"
    seconds: float
    timeline: ShotSignals
    text: TextSignals
    starts: list[int]
    settings: TextSettings
    stored_captions: int


def _load_cases(home: Path, raw_names: set[str]) -> list[Case]:
    app = Application.start(StartupOptions(home=home))
    try:
        library = app.container.resolve(MediaLibrary)
        catalog = app.container.resolve(VideoCatalog)
        cases: list[Case] = []
        page = 1
        while True:
            found = library.search(
                MediaQuery(media_types=frozenset({MediaType.VIDEO}), page=page, page_size=200)
            )
            for asset in found.items:
                latest = catalog.latest(asset.id)
                if not isinstance(latest, Ok) or latest.value is None:
                    continue
                analysis, stored = latest.value
                ids = stored.metadata.get("signal_asset_ids")
                if not isinstance(ids, dict) or "text" not in ids or "shots" not in ids:
                    continue
                docs: dict[str, object] = {}
                for name, tp in (("text", TextSignals), ("shots", ShotSignals)):
                    path = library.local_path(str(ids[name]))
                    if not isinstance(path, Ok):
                        break
                    docs[name] = document_from_json(
                        signals_kind(AnalyzerId(name)), tp, path.value.read_text("utf-8")
                    )
                else:
                    timeline = docs["shots"]
                    text = docs["text"]
                    if not isinstance(timeline, ShotSignals) or not isinstance(text, TextSignals):
                        continue
                    profile = get_profile(analysis.provenance.profile_name)
                    detection = detect_transitions(timeline, profile.shots)
                    original = asset.original_filename
                    cases.append(
                        Case(
                            name=asset.display_name,
                            label="negative" if original in raw_names else "unlabelled",
                            seconds=timeline.end_time().seconds - timeline.time_of(0).seconds,
                            timeline=timeline,
                            text=text,
                            starts=[0, *(t.frame for t in detection.transitions)],
                            settings=profile.text,
                            stored_captions=sum(
                                1
                                for o in analysis.overlays
                                if o.kind is OverlayKind.BURNED_IN_CAPTION
                            ),
                        )
                    )
            if not found.has_next:
                return cases
            page += 1
    finally:
        app.shutdown()


# --- synthetic positives / negatives (no client text) ----------------------------------------
_WORDS = (
    "heute zeige ich euch wie man den neuen wecker richtig einstellt und warum das so "
    "wichtig ist weil viele leute morgens einfach zu spät aufstehen genau deshalb haben "
    "wir alles getestet über drei wochen lang mit verschiedenen geräten größe preis "
    "qualität und am ende gibt es eine klare empfehlung für jeden"
).split()


def _noisy(text: str, rnd: random.Random) -> str:
    """An OCR read: sometimes one character wrong, sometimes an umlaut lost."""
    if rnd.random() < 0.25 and text:
        i = rnd.randrange(len(text))
        text = text[:i] + rnd.choice("aeinorstl0") + text[i + 1 :]
    if rnd.random() < 0.2:
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return text


def _synthetic_caption_clip(
    rnd: random.Random, style: str, fps: float = 25.0
) -> tuple[ShotSignals, TextSignals]:
    """A 6-40 s single-shot clip with changing captions read by OCR about once a second.

    Styles: one line of 2-4 words, two lines (two boxes) or word-by-word (1-2 words, short).
    """
    frames = int(rnd.randint(6, 40) * fps)
    stride = int(fps)
    shown: list[tuple[int, int, list[str]]] = []  # start, end, lines
    t = 0
    while t < frames:
        if style == "words":
            hold = int(rnd.uniform(0.5, 1.2) * fps)
            lines = [" ".join(rnd.sample(_WORDS, rnd.randint(1, 2)))]
        elif style == "rolling":  # the line grows word by word, then a new sentence starts
            sentence = rnd.sample(_WORDS, rnd.randint(4, 6))
            for n in range(2, len(sentence) + 1):
                hold = int(rnd.uniform(0.6, 1.2) * fps)
                shown.append((t, t + hold, [" ".join(sentence[:n])]))
                t += hold
            continue
        else:
            hold = int(rnd.uniform(1.2, 3.0) * fps)
            count = 2 if style == "two_lines" else 1
            lines = [" ".join(rnd.sample(_WORDS, rnd.randint(2, 4))) for _ in range(count)]
        shown.append((t, t + hold, lines))
        t += hold
    rows: list[TextRow] = []
    for f in range(0, frames, stride):
        lines = next(ls for a, b, ls in shown if a <= f < b)
        for n, line in enumerate(lines):
            if rnd.random() < 0.1:
                continue  # missed read
            y = 0.78 + 0.08 * n
            half = min(0.45, 0.01 * len(line))
            box = BBox(0.5 - half, y, 0.5 + half, y + 0.06)
            rows.append(TextRow(f, _noisy(line, rnd), rnd.uniform(0.6, 0.98), box))
    return _timeline(frames, fps), TextSignals(
        "synthetic", tuple(range(0, frames, stride)), tuple(rows)
    )


def _synthetic_static_misread(
    rnd: random.Random, fps: float = 25.0
) -> tuple[ShotSignals, TextSignals]:
    """A static label low in the picture, misread differently about every second.

    Half the clips: a 2-3 character code ("9AX", "AF", ...); half: a short word ("OPEN", "0PEN").
    """
    frames = int(rnd.randint(20, 60) * fps)
    reads = (
        ("9AX", "AF", "AE", "9AK", "QAX", "8AX")
        if rnd.random() < 0.5
        else ("OPEN", "0PEN", "OPFN", "QPEN", "OPEM", "OPE N")
    )
    rows = tuple(
        TextRow(f, rnd.choice(reads), rnd.uniform(0.5, 0.8), BBox(0.6, 0.75, 0.66, 0.79))
        for f in range(0, frames, int(fps))
        if rnd.random() < 0.7
    )
    return _timeline(frames, fps), TextSignals("synthetic", tuple(range(0, frames, int(fps))), rows)


STYLES = ("line", "two_lines", "words", "rolling")


def _timeline(frames: int, fps: float) -> ShotSignals:
    zeros = tuple(0.0 for _ in range(frames))
    return ShotSignals(
        timebase=Rational(1, int(fps)),
        pts=tuple(range(frames)),
        end_pts=frames,
        estimated_timestamps=0,
        long_gap=8,
        luma_mean=tuple(0.5 for _ in range(frames)),
        luma_std=tuple(0.2 for _ in range(frames)),
        diff1=zeros,
        diff2=zeros,
        diff_long=zeros,
        hist1=zeros,
    )


# --- metrics ---------------------------------------------------------------------------------
def _shipped_spans(case: Case, items: Sequence[TrackedText]) -> list[Span]:
    t = case.timeline
    return [
        (o.first.frame, o.last.frame)
        for o in find_overlays(items, case.text, t, case.settings)
        if o.kind is OverlayKind.BURNED_IN_CAPTION
    ]


def _seconds(t: ShotSignals, span: Span) -> float:
    return t.time_of(span[1]).seconds - t.time_of(span[0]).seconds


def main() -> None:
    global SAME_RATIO
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--raw", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--synthetic", type=int, default=400)
    parser.add_argument("--same-ratio", type=float, default=SAME_RATIO)
    args = parser.parse_args()
    SAME_RATIO = args.same_ratio
    raw_names = {p.name for d in args.raw for p in d.iterdir() if p.is_file()}

    rules: dict[str, Rule] = {
        "legacy": legacy,
        "wordlike": wordlike,
        "ordered": ordered,
        "centred": centred,
        "uncrowded": uncrowded,
        "unc+order": uncrowded_ordered,
    }
    names = [*rules, "shipped"]
    cases = _load_cases(args.home, raw_names)

    per_asset: list[dict[str, object]] = []
    totals: dict[str, dict[str, dict[str, float]]] = {}
    for case in cases:
        items = track_text(case.text, case.starts, case.settings)
        spans = {
            n: _legacy_caption_spans(items, len(case.text.frames), case.settings, r)
            for n, r in rules.items()
        }
        spans["shipped"] = _shipped_spans(case, items)
        bucket = totals.setdefault(case.label, {})
        for n in names:
            agg = bucket.setdefault(n, {"spans": 0.0, "seconds": 0.0})
            agg["spans"] += len(spans[n])
            agg["seconds"] += sum(_seconds(case.timeline, s) for s in spans[n])
        bucket.setdefault("_footage", {"seconds": 0.0, "assets": 0.0})
        bucket["_footage"]["seconds"] += case.seconds
        bucket["_footage"]["assets"] += 1
        bottom = [i for i in items if i.box.y0 >= case.settings.caption_top]
        per_asset.append(
            {
                "asset": case.name,
                "label": case.label,
                "seconds": round(case.seconds, 2),
                "ocr_rows": len(case.text.rows),
                "stored_caption_overlays": case.stored_captions,
                "spans": {
                    n: [
                        [
                            round(case.timeline.time_of(a).seconds, 2),
                            round(case.timeline.time_of(b).seconds, 2),
                        ]
                        for a, b in s
                    ]
                    for n, s in spans.items()
                },
                "bottom_band_keys": sorted({i.key for i in bottom}),
            }
        )

    # synthetic recall (per caption style) / false captions on static misreads
    rnd = random.Random(20261111)
    groups = [*STYLES, "static"]
    hits: dict[str, dict[str, int]] = {n: dict.fromkeys(groups, 0) for n in names}
    settings = TextSettings()
    for group in groups:
        for _ in range(args.synthetic):
            timeline, text = (
                _synthetic_static_misread(rnd)
                if group == "static"
                else _synthetic_caption_clip(rnd, group)
            )
            case = Case("synthetic", group, 0.0, timeline, text, [0], settings, 0)
            items = track_text(text, [0], settings)
            found = {
                n: _legacy_caption_spans(items, len(text.frames), settings, r)
                for n, r in rules.items()
            }
            found["shipped"] = _shipped_spans(case, items)
            for n in names:
                hits[n][group] += bool(found[n])

    summary: dict[str, object] = {}
    print(f"{'set':<11}{'rule':<11}{'assets':>7}{'hours':>8}{'spans':>7}{'spans/h':>9}{'sec/h':>9}")
    for label, bucket in sorted(totals.items()):
        hours = bucket["_footage"]["seconds"] / 3600
        for n in names:
            row = {
                "assets": int(bucket["_footage"]["assets"]),
                "hours": round(hours, 3),
                "spans": int(bucket[n]["spans"]),
                "spans_per_hour": round(bucket[n]["spans"] / hours, 2) if hours else 0.0,
                "seconds_per_hour": round(bucket[n]["seconds"] / hours, 1) if hours else 0.0,
            }
            summary[f"{label}/{n}"] = row
            print(
                f"{label:<11}{n:<11}{row['assets']:>7}{row['hours']:>8}{row['spans']:>7}"
                f"{row['spans_per_hour']:>9}{row['seconds_per_hour']:>9}"
            )
    print(f"\nsynthetic, {args.synthetic} clips per column (same_ratio {SAME_RATIO}):")
    print("recall per caption style; 'static' = false-caption rate on static misreads")
    print(f"{'rule':<11}" + "".join(f"{g:>10}" for g in groups))
    for n in names:
        rates = {g: hits[n][g] / args.synthetic for g in groups}
        summary[f"synthetic/{n}"] = rates
        print(f"{n:<11}" + "".join(f"{rates[g]:>10.3f}" for g in groups))
    legacy_ok = all(
        a["stored_caption_overlays"] == len(a["spans"]["legacy"])  # type: ignore[index]
        for a in per_asset
    )
    print(f"\nlegacy re-run matches stored caption overlays: {legacy_ok}")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "caption_rule_results.json").write_text(
        json.dumps({"summary": summary, "assets": per_asset}, ensure_ascii=False, indent=2),
        "utf-8",
    )
    print(f"details: {args.out / 'caption_rule_results.json'}")


if __name__ == "__main__":
    main()
