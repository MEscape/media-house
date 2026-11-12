"""Fact table, Technik-Slots, script resolution, claim risks and the script linter."""

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from media_house.modules.production_project.domain.brief import BriefModel, Fact
from media_house.modules.production_project.domain.brief_parser import parse_brief
from media_house.modules.production_project.domain.claims import (
    ClaimKind,
    ScanContext,
    outdated_facts,
    quality_risks,
    scan_plan,
    scan_text,
)
from media_house.modules.production_project.domain.compliance import (
    LintContext,
    RuleKind,
    compile_rules,
    lint_script,
)
from media_house.modules.production_project.domain.facts import (
    dependants,
    derive,
    display,
    products,
    spec_ratio_formulas,
    standard_formulas,
    substitute_facts,
    with_value,
)
from media_house.modules.production_project.domain.numbers import (
    format_number,
    parse_number,
    slugify,
)
from media_house.modules.production_project.domain.script import Exclusion, parse_script
from media_house.modules.production_project.domain.script_tools import (
    estimate_lengths,
    render_script,
    resolve_script,
)
from media_house.modules.production_project.domain.slots import (
    fill,
    fill_values,
    slot_facts,
    standard_slots,
)
from media_house.modules.production_project.domain.values import Severity

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "brief_to_cut" / "lidl_brief.txt"


@pytest.fixture(scope="module")
def brief() -> BriefModel:
    return parse_brief(FIXTURE.read_text(encoding="utf-8"), reference_year=2026)


def test_german_numbers_are_exact() -> None:
    assert parse_number("14,99 €") == Fraction(1499, 100)
    assert parse_number("1.299,50") == Fraction(129950, 100)
    assert parse_number("1.299") == 1299
    assert format_number(Fraction(129950, 100)) == "1.299,50"
    assert format_number(Fraction(1, 3), 1) == "0,3"
    assert format_number(Fraction(5, 2), 0) == "3"
    assert slugify("Fontänen-Box Süß") == "fontaenen-box-suess"


def test_derived_numbers_are_computed_by_code(brief: BriefModel) -> None:
    facts = derive(brief.facts, standard_formulas(brief.facts))
    by_key = {f.key: f for f in facts}

    assert by_key["price_total"].value == "69,95"
    assert by_key["price_min"].value == "6,99"
    assert by_key["price_max"].value == "24,99"
    assert by_key["price_spread"].value == "18,00"
    assert by_key["product_count"].value == "5"
    assert by_key["price_total"].derived_from[0].startswith("price_")
    assert not by_key["price_total"].verified  # derived facts rest on unverified inputs


def test_a_changed_price_unverifies_everything_derived_from_it(brief: BriefModel) -> None:
    facts = tuple(
        replace(f, verified=True) for f in derive(brief.facts, standard_formulas(brief.facts))
    )
    key = "price_raketenset-orion"

    # structural: the total, the extremes and everything computed from them rest on every price
    assert {"price_total", "price_min", "price_max", "price_spread"} <= set(dependants(facts, key))
    assert "product_count" in dependants(facts, key)
    changed = {f.key: f for f in with_value(facts, key, "10,99")}
    assert changed[key].value == "10,99" and not changed[key].verified
    assert not changed["price_total"].verified and not changed["price_spread"].verified
    assert changed["price_feuerwerksbatterie-nova"].verified  # an unrelated input stays verified


def test_facts_are_shown_the_german_way_and_substituted_by_key(brief: BriefModel) -> None:
    facts = derive(brief.facts, standard_formulas(brief.facts))
    text, missing = substitute_facts(
        "Das Set kostet {{fact:price_raketenset-orion}}, "
        "bestellen bis {{fact:order_window}} {{fact:nope}}.",
        facts,
    )

    assert text.startswith("Das Set kostet 9,99 €, bestellen bis 9.11.2026 bis 22.11.2026")
    assert missing == ("nope",)
    assert display(Fact("x", "x", "5", "mm", None, approximate=True)) == "ca. 5 mm"


def test_ratios_from_technik_fields_are_approximations(brief: BriefModel) -> None:
    rows = products(brief.facts)
    slots = standard_slots(rows)
    nova = rows[0].subject
    for stem, value in (("shots", "100"), ("effektmasse", "250")):
        key = next(s.key for s in slots if s.subject == nova and s.stem == stem)
        slots = fill(slots, key, value)

    facts = derive(
        (*brief.facts, *slot_facts(slots)), spec_ratio_formulas((*brief.facts, *slot_facts(slots)))
    )
    ratio = next(f for f in facts if f.key == f"mass_per_shot_{nova}")

    assert ratio.value == "2,5" and ratio.approximate and ratio.unit == "g"
    assert display(ratio) == "ca. 2,5 g"


SCRIPT = """# Transcript

## S01 · ca. 0:00-0:09 · Einstieg
Absicht: Aufhänger.
Funktion: hook
Grafik: Titel mit {{fact:product_count}} Produkten

SPRECHTEXT:
Heute zeige ich dir fünf Artikel.

## S02 · ca. 0:09-0:40 · Das Set
Funktion: product

SPRECHTEXT:
Das Set kostet {{fact:price_raketenset-orion}}. Es hat {SCHUSS_ORION} Schuss. [VERIFY]
[OPTIONAL: spedition]
Die Lieferung kommt per Spedition.
[/OPTIONAL]
[CONDITIONAL: needs approval]
Das gibt es sonst bei keinem Discounter.
[/CONDITIONAL]
"""


def test_the_script_reader_keeps_tags_blocks_gates_and_graphics() -> None:
    script = parse_script(SCRIPT)
    first, second = script.scenes

    assert first.function == "hook" and first.time_hint == "0:00-0:09"
    assert first.graphics_intent == "Titel mit {{fact:product_count}} Produkten"
    sentences = {s.text: s for s in second.sentences}
    assert sentences["Es hat {SCHUSS_ORION} Schuss."].verify
    assert sentences["Es hat {SCHUSS_ORION} Schuss."].exclusion is Exclusion.UNFILLED_SLOT
    assert sentences["Die Lieferung kommt per Spedition."].block == "spedition"
    gated = sentences["Das gibt es sonst bei keinem Discounter."]
    assert (
        gated.exclusion is Exclusion.APPROVAL_REQUIRED
        and gated.gate == "CONDITIONAL: NEEDS APPROVAL"
    )
    assert second.tags == ("OPTIONAL", "CONDITIONAL", "SLOT", "VERIFY")


def test_resolving_fills_slots_facts_and_approved_gates(brief: BriefModel) -> None:
    facts = derive(brief.facts, standard_formulas(brief.facts))
    script = parse_script(SCRIPT)

    spoken = resolve_script(
        script,
        slots={"SCHUSS_ORION": "42"},
        facts=facts,
        approvals=("CONDITIONAL: NEEDS APPROVAL",),
    )
    texts = {s.text: s for s in spoken.scenes[1].sentences}

    assert "Das Set kostet 9,99 €." in texts
    assert "Es hat 42 Schuss." in texts and texts["Es hat 42 Schuss."].usable
    assert texts["Das gibt es sonst bei keinem Discounter."].usable
    assert spoken.scenes[0].graphics_intent == "Titel mit 5 Produkten"
    # the stored script keeps its placeholders: one source of truth
    assert "{SCHUSS_ORION}" in script.scenes[1].sentences[1].text


def test_the_rendered_transcript_reads_back_the_same(brief: BriefModel) -> None:
    script = parse_script(SCRIPT)
    text = render_script(
        script, title="Test", facts=brief.facts[:2], slot_rows=[("A", "Kaliber", "")]
    )
    again = parse_script(text)

    def shape(s: object) -> list[tuple[str, str, str, bool, str, str, bool]]:
        return [
            (sc.scene_id, sn.text, sn.exclusion.value, sn.optional, sn.block, sn.gate, sn.verify)
            for sc in s.scenes  # type: ignore[attr-defined]
            for sn in sc.sentences
        ]

    assert shape(again) == shape(script)
    assert [s.function for s in again.scenes] == ["hook", "product"]
    assert "| A | Kaliber | (offen) |" in text and "## Fakten-Tabelle" in text


def test_length_estimate_skips_excluded_and_switched_off_blocks() -> None:
    script = resolve_script(parse_script(SCRIPT), slots={}, facts=())

    full = sum(i.seconds for i in estimate_lengths(script))
    trimmed = sum(i.seconds for i in estimate_lengths(script, off_blocks=("spedition",)))

    assert trimmed < full


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (
            "Das hat es noch nie als erstes bei einem Discounter gegeben, als erster zeige ich es.",
            ClaimKind.FIRST,
        ),
        ("Das gibt es bei keinem anderen Discounter.", ClaimKind.EXCLUSIVITY),
        ("Ich garantiere euch, dass ihr das nicht kennt.", ClaimKind.GUARANTEE),
        ("Das ist der beste Böller von allen.", ClaimKind.SUPERLATIVE),
        ("Bei Aldi kostet das mehr.", ClaimKind.COMPETITOR),
        ("Die Qualität von Lidl ist schlecht.", ClaimKind.NEGATIVE_CLIENT),
        ("Polenböller sind natürlich besser.", ClaimKind.ILLEGAL_PYRO),
        ("Weco macht das auch.", ClaimKind.THIRD_PARTY_BRAND),
    ],
)
def test_risky_claims_are_found_with_a_reason_and_a_safe_alternative(
    text: str, kind: ClaimKind
) -> None:
    risks = scan_text(text, "idea", ScanContext(client="Lidl"))

    hit = next(r for r in risks if r.kind is kind)
    assert hit.why and hit.safe_alternative and hit.quote


def test_a_superlative_inside_the_offer_is_fine() -> None:
    assert (
        scan_text("Unter diesen fünf ist das der beste Effekt.", "S01", ScanContext("Lidl")) == ()
    )


def test_confidential_material_the_creator_wants_to_show_is_flagged(brief: BriefModel) -> None:
    risks = scan_plan(
        "Ich will die Prospektseite mit den Vorschau-Preisen im Bild zeigen.",
        "idea",
        brief,
        ScanContext("Lidl"),
    )

    (risk,) = [r for r in risks if r.kind is ClaimKind.CONFIDENTIAL_SHOWN]
    assert risk.severity is Severity.BLOCKER and risk.constraint_id


def test_archive_material_needs_approval_first(brief: BriefModel) -> None:
    risks = scan_plan(
        "Dazu nehme ich Archivmaterial älterer Prospekte.", "idea", brief, ScanContext("Lidl")
    )

    assert any(r.kind is ClaimKind.CONDITIONAL_USED for r in risks)


def test_a_date_from_another_year_is_an_outdated_fact() -> None:
    brief = parse_brief("Bestellung: 09.-22.11.2025 im Shop.", reference_year=2026)

    (risk,) = outdated_facts(brief, 2026)
    assert "2025" in risk.why


def test_shot_count_only_product_talk_is_a_quality_warning() -> None:
    script = parse_script(
        "## S02 · ca. 0:00-0:10 · Set\nFunktion: product\n\nSPRECHTEXT:\nDas Set hat 100 Schuss.\n"
    )

    assert quality_risks(script, ())[0].kind is ClaimKind.QUALITY
    better = parse_script(
        "## S02 · ca. 0:00-0:10 · Set\nFunktion: product\n\nSPRECHTEXT:\n"
        "Das Set hat 100 Schuss und einen roten Effekt.\n"
    )
    assert quality_risks(better, ()) == ()


def test_the_linter_catches_wrong_numbers_unfilled_slots_gates_and_a_late_hook(
    brief: BriefModel,
) -> None:
    facts = derive(brief.facts, standard_formulas(brief.facts))
    raw = parse_script(
        "## S01 · ca. 0:00-0:20 · Einstieg\nFunktion: context\n\nSPRECHTEXT:\n"
        "Das ist ein ziemlich langer erster Satz, der viel zu spät zum eigentlichen Punkt kommt "
        "und kein Ende nimmt.\n"
        "Das Set kostet 9,95 €. Bestellen bis 23.11. Es hat {SCHUSS_ORION} Schuss.\n"
        "[NUR_MIT_X_FREIGABE]\nGeheimes Detail.\n[/NUR_MIT_X_FREIGABE]\n"
    )
    spoken = resolve_script(raw, slots={}, facts=facts)
    ctx = LintContext(compile_rules(brief), facts, ScanContext("Lidl"), target_minutes=(8.0, 10.0))

    found = {f.rule_id for f in lint_script(spoken, ctx, estimated_seconds=60)}

    assert {
        "numbers.price_not_in_fact_table",
        "numbers.date_not_in_fact_table",
        "slots.unfilled",
        "gate.needs_approval",
        "hook.first_five_seconds",
        "length.too_short",
    } <= found


def test_every_brief_constraint_becomes_a_rule_and_an_unknown_one_is_manual(
    brief: BriefModel,
) -> None:
    rules = compile_rules(brief)

    assert len(rules.rules) == len(brief.constraints)
    assert (
        rules.has(RuleKind.LABEL)
        and rules.has(RuleKind.COMPETITOR)
        and rules.has(RuleKind.CONFIDENTIAL)
    )
    manual = compile_rules(parse_brief("Die Aufnahmen müssen in Farbe sein.", reference_year=2026))
    assert [r.kind for r in manual.rules] == [RuleKind.MANUAL]


def test_slots_do_not_exist_twice_and_fill_values_only_the_filled(brief: BriefModel) -> None:
    slots = standard_slots(products(brief.facts))
    assert len({s.key for s in slots}) == len(slots) == 25
    assert all(s.name.startswith("{") and s.key[0].isalpha() for s in slots)
    filled = fill(slots, slots[0].key, " 30 ")
    assert fill_values(filled) == {slots[0].key: "30"}
    assert slots[0].key == "KALIBER_FEUERWERKSBATTERIE_NOVA"
