"""Proxy scoring of ASR hypotheses against the owner's planned script (no verbatim reference).

* ``spoken_script`` keeps only the SPRECHTEXT parts of the script markdown (stage directions in
  ``[...]`` and ``{SLOT}`` placeholders removed, optional/approval blocks kept).
* ``best_span`` aligns a hypothesis to the best-matching contiguous span of the script
  (semi-global edit distance: skipping script text before/after the span is free).
* Script-WER (proxy) = edits / words of the matched script span. Spoken deviations from the
  script count as errors for every model alike, so the number ranks models; it is not accuracy.
* Numeric entities: scripted numbers that also appear in the script's fact tables and carry a
  decimal comma or a unit (Euro, Schuss, Meter, Gramm, Sekunden). A hit needs the same digits
  (decimal comma, thousands dot optional) followed by the unit in the hypothesis.

Run ``python script_wer.py --selftest`` for the built-in checks. No client text lives here.
"""

# ruff: noqa: T201, S101

import difflib
import re
import sys
from collections import Counter

_TOKEN = re.compile(r"\d+(?:[.,:]\d+)*|[^\W\d_]+")
_THOUSANDS_DOT = re.compile(r"(?<=\d)\.(?=\d{3}(?!\d))")
_UNITS = {
    "euro": {"euro", "eur"},
    "schuss": {"schuss"},
    "meter": {"meter", "metern", "m"},
    "gramm": {"gramm", "g"},
    "sekunden": {"sekunden", "sek", "s"},
}
_UNIT_OF = {form: unit for unit, forms in _UNITS.items() for form in forms}


def words(text: str) -> list[str]:
    """Lower-case word/number tokens; ``€`` -> ``euro``, ``ß`` -> ``ss``, ``1.183`` -> ``1183``."""
    text = text.lower().replace("€", " euro ").replace("%", " prozent ").replace("ß", "ss")
    return _TOKEN.findall(_THOUSANDS_DOT.sub("", text))


def spoken_script(markdown: str) -> str:
    lines: list[str] = []
    speaking = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            speaking = False
        elif line.strip().startswith("SPRECHTEXT"):
            speaking = True
        elif speaking and not line.lstrip().startswith("["):  # whole-line [..] = stage direction
            lines.append(line)
    text = re.sub(r"\{[^}]*\}", " ", " ".join(lines))
    return re.sub(r"\[[^\]]*\]", " ", text)  # inline markers such as [OPTIONAL]


def fact_numbers(markdown: str) -> set[str]:
    """Number tokens of the '## Fakten-Tabelle' section (all its tables)."""
    section = markdown.split("## Fakten-Tabelle", 1)[-1].split("\n## ", 1)[0]
    return {w for w in words(section) if w[0].isdigit()}


def best_span(hyp: list[str], ref: list[str]) -> tuple[int, int, int]:
    """(edits, start, end): min edit distance of ``hyp`` to any ``ref[start:end]``."""
    n = len(hyp)
    cost = list(range(n + 1))
    start = [0] * (n + 1)
    best = (n, 0, 0)
    for j, r in enumerate(ref, 1):
        new_cost, new_start = [0], [j]  # free start at every script position
        for i in range(1, n + 1):
            options = (
                (cost[i - 1] + (hyp[i - 1] != r), start[i - 1]),  # match / substitution
                (new_cost[i - 1] + 1, new_start[i - 1]),  # inserted hyp word
                (cost[i] + 1, start[i]),  # missed script word
            )
            c, s = min(options, key=lambda o: o[0])
            new_cost.append(c)
            new_start.append(s)
        cost, start = new_cost, new_start
        if cost[n] < best[0]:
            best = (cost[n], start[n], j)
    return best


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1]


def sentences(text: str, min_words: int = 4) -> list[str]:
    """Split on . ! ? and merge fragments shorter than ``min_words`` into the next one."""
    out: list[str] = []
    carry = ""
    for part in re.split(r"(?<=[.!?])\s+", text.strip()):
        carry = f"{carry} {part}".strip()
        if len(words(carry)) >= min_words:
            out.append(carry)
            carry = ""
    if carry:
        out.append(carry)
    return out


def entities(ref: list[str], facts: set[str]) -> list[tuple[int, str, str | None]]:
    """(script position, number, unit) of every scored numeric entity in the script."""
    found = []
    for p, w in enumerate(ref):
        if not w[0].isdigit() or w not in facts:
            continue
        unit = _UNIT_OF.get(ref[p + 1]) if p + 1 < len(ref) else None
        if unit or "," in w:
            found.append((p, w, unit))
    return found


def entity_hits(hyp: list[str], wanted: Counter[tuple[str, str | None]]) -> int:
    seen: Counter[tuple[str, str | None]] = Counter()
    for p, w in enumerate(hyp):
        unit = _UNIT_OF.get(hyp[p + 1]) if p + 1 < len(hyp) else None
        seen[(w, unit)] += 1
        if unit:
            seen[(w, None)] += 1  # a scripted unit-less price may be spoken with "Euro"
    return sum(min(seen[key], n) for key, n in wanted.items())


def diff_snippets(ref: list[str], hyp: list[str], context: int = 2) -> list[str]:
    """Compact 'ref -> hyp' snippets of each differing chunk with a little context."""
    out = []
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        left = " ".join(ref[max(0, a0 - context) : a0])
        right = " ".join(ref[a1 : a1 + context])
        out.append(
            f"…{left} [{' '.join(ref[a0:a1]) or '∅'} → {' '.join(hyp[b0:b1]) or '∅'}] {right}…"
        )
    return out


def _selftest() -> None:
    assert words("Pure Impact: 79,99 €, 1.183 Gramm, 4:30 Uhr, Größe") == [
        "pure", "impact", "79,99", "euro", "1183", "gramm", "4:30", "uhr", "grösse",
    ]  # fmt: skip
    assert words("79.99 Euro") == ["79.99", "euro"]  # decimal point is not the German format
    ref = words("a b c d e f g h")
    assert best_span(words("c d e"), ref) == (0, 2, 5)
    assert best_span(words("c x e"), ref) == (1, 2, 5)
    assert best_span(words("c d d e"), ref)[0] == 1
    md = (
        "## S1\nSPRECHTEXT:\n[Regie]\nHallo {SLOT: „x“} [OPTIONAL]Welt[/OPTIONAL].\n"
        "## Fakten-Tabelle\n| 99,99 € | 50 m |\n"
    )
    assert words(spoken_script(md)) == ["hallo", "welt"]
    assert fact_numbers(md) == {"99,99", "50"}
    script = words("Kostet 99,99 Euro, fliegt 50 Meter, Seite 50 hat 3 Meter")
    ents = entities(script, {"99,99", "50"})
    assert [(n, u) for _, n, u in ents] == [("99,99", "euro"), ("50", "meter")]
    wanted = Counter((n, u) for _, n, u in ents)
    assert entity_hits(words("kostet 99,99 € und 50 m"), wanted) == 2
    assert entity_hits(words("kostet 99.99 euro und fünfzig meter"), wanted) == 0
    assert edit_distance(words("a b c"), words("a x c d")) == 2
    assert sentences("Ja. Das ist gut so. Okay!") == ["Ja. Das ist gut so.", "Okay!"]
    print("selftest ok")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
