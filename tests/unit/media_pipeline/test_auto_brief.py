"""The automatic brief of a synthetic project folder (fake files, synthetic German speech)."""

from media_house.modules.media_pipeline.application.auto_brief import apply_auto_brief
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.layer4_inputs import GraphicWish
from media_house.modules.media_pipeline.domain.auto_brief import (
    AutoBrief,
    BriefInputs,
    Footage,
    Line,
    Product,
    Role,
    ScreenText,
    auto_brief,
    role_of,
    static_bands,
)


def _f(asset_id: str, name: str, role: Role, *, video: bool = True, **more: object) -> Footage:
    return Footage(asset_id=asset_id, name=name, role=role, video=video, **more)  # type: ignore[arg-type]


FOOTAGE = (
    _f("cam1", "proj_talking_1", Role.CAMERA, spoken_words=200),
    _f("cam2", "proj_talking_2", Role.CAMERA, spoken_words=50),
    _f("cam3", "proj_talking_3", Role.CAMERA, spoken_words=150),
    _f("gag0", "proj_wecker_gag_0", Role.CAMERA, duration=6.0, shots=((0.0, 6.0),)),
    _f("gag1", "proj_wecker_gag_1", Role.CAMERA, duration=6.0),
    _f("gag2", "proj_wecker_gag_2", Role.CAMERA, duration=6.0),
    _f("react", "proj_reaktionen", Role.CAMERA, duration=5.0),
    _f("crowd", "acme_ansturm", Role.ARCHIVE, duration=30.0, shots=((0.0, 10.0), (10.0, 30.0))),
    _f(
        "scr",
        "proj_shop_seite",
        Role.SCREEN,
        duration=120.0,
        shots=((0.0, 3.0), (3.0, 60.0), (60.0, 120.0)),
    ),
    _f("cat1", "acme_katalog_2122", Role.ARCHIVE, video=False),
    _f("cat2", "acme_katalog_2223", Role.ARCHIVE, video=False),
    _f("catnew", "katalog-neu", Role.PACKSHOT, video=False),
    _f("bg", "sterne", Role.BACKGROUND),
    _f("introfull", "kanal_intro_full", Role.CHANNEL),
    _f("intro", "kanal_intro", Role.CHANNEL),
    _f("outro", "kanal_outro", Role.CHANNEL),
    _f("logo_a", "Acme-Logo", Role.CHANNEL, video=False),
    _f("logo_p", "Pyro-Werk", Role.CHANNEL, video=False),
)
SPEECH = (
    ("cam1", "Es ist 5 Uhr und alle stehen schon an."),
    ("cam1", "Aufstehen, anziehen und ab in den Ansturm vor dem Laden."),
    ("cam1", "Aber heute zeige ich euch eine bessere Lösung."),
    ("cam1", "Drei neue Kracher von Pyro bei Acme."),
    ("cam1", "Alles ist direkt online bestellbar."),
    ("cam1", "Früher hieß es Katalog wälzen und hoffen."),
    ("cam1", "Dieses Jahr geht alles bequem online."),
    ("cam1", "Bestellen kannst du bis zum 22. November."),
    ("cam2", "Und so sieht das Ganze im Shop aus."),
    ("cam2", "Einfach die Seite aufrufen und fertig."),
    ("cam3", "Alpha Rakete kostet 19,99 Euro."),
    ("cam3", "Beta Fontäne kostet 24,99 Euro."),
    ("cam3", "Gamma Batterie kostet 49,99 Euro."),
    ("cam3", "Wenn man die drei Kracher nebeneinander stellt, sieht man den Unterschied."),
    ("cam3", "Dieses Video ist Werbung in Zusammenarbeit mit Acme."),
    ("cam3", "Lasst gerne ein Abo da."),
)


def _lines() -> tuple[Line, ...]:
    out, at = [], {"cam1": 0.0, "cam2": 0.0, "cam3": 0.0}
    for asset_id, text in SPEECH:
        start = at[asset_id]
        at[asset_id] = start + 3.0
        scene = "Screenrecording: so läuft die Bestellung" if asset_id == "cam2" else ""
        out.append(Line(asset_id=asset_id, start=start, end=start + 3.0, text=text, scene=scene))
    return tuple(out)


def _product(name: str, price: str, image: str) -> Product:
    return Product(
        name=name,
        image=image,
        facts=(("Nr.", "1/3"), ("Preis", price), ("Schuss", "25"), ("Höhe", "ca. 30 m")),
    )


PRODUCTS = (
    _product("Alpha Rakete", "19,99 €", "img_a"),
    _product("Beta Fontäne", "24,99 €", "img_b"),
    _product("Gamma Batterie", "49,99 €", "img_c"),
)


def _brief(**more: object) -> AutoBrief:
    inputs = {
        "format_profile": "ranking",
        "sponsored": True,
        "title": "Kracher online bestellen",
        "footage": FOOTAGE,
        "lines": _lines(),
        "products": PRODUCTS,
        "product_cards": True,
    }
    return auto_brief(BriefInputs(**(inputs | more)))  # type: ignore[arg-type]


def _graphic(brief: AutoBrief, kind: str, say: str) -> list[dict[str, object]]:
    return [dict(g.slots) for g in brief.graphics if g.kind == kind and g.say.startswith(say)]


def test_folder_roles_follow_the_numbered_convention() -> None:
    assert role_of("01_cam") is Role.CAMERA
    assert role_of("06_GFX") is Role.CHANNEL
    assert role_of("07_thumb") is None
    assert role_of("random") is None


def test_title_card_at_the_premise_turn_only_with_a_title() -> None:
    assert _graphic(_brief(), "title", "Aber heute zeige") == [
        {"title": "Kracher online bestellen"}
    ]
    assert not [g for g in _brief(title="").graphics if g.kind == "title"]


def test_counted_products_get_a_teaser_and_the_brands_move_to_the_next_sentence() -> None:
    brief = _brief()
    assert _graphic(brief, "product_sticker", "Drei neue Kracher") == [
        {"image": "img_a"},
        {"image": "img_b"},
    ]
    assert _graphic(brief, "product_sticker", "Alles ist direkt") == [
        {"image": "logo_a"},
        {"image": "logo_p"},
    ]
    assert _graphic(brief, "product_sticker", "Dieses Video ist") == [{"image": "logo_a"}]
    assert not _graphic(_brief(sponsored=False), "product_sticker", "Dieses Video ist")


def test_archive_series_gallery_then_this_years_page() -> None:
    brief = _brief()
    assert _graphic(brief, "product_sticker", "Früher hieß es") == [
        {"captions": ("2021/22", "2022/23"), "images": ("cat1", "cat2"), "title": "Katalog"}
    ]
    assert _graphic(brief, "product_sticker", "Dieses Jahr geht") == [
        {"captions": ("Neu",), "images": ("catnew",), "title": "Dieses Jahr"}
    ]


def test_matrix_deadline_and_cta() -> None:
    brief = _brief()
    assert _graphic(brief, "comparison_matrix", "Wenn man die") == [
        {
            "columns": ("Preis", "Schuss", "Höhe"),
            "rows": (
                ("Alpha Rakete", "19,99 €", "25", "30 m"),
                ("Beta Fontäne", "24,99 €", "25", "30 m"),
                ("Gamma Batterie", "49,99 €", "25", "30 m"),
            ),
            "title": "Alle 3 im Vergleich",
        }
    ]
    assert _graphic(brief, "deadline_chip", "Bestellen kannst du") == [
        {"date": "22.11.", "label": "NUR BIS"}
    ]
    assert _graphic(brief, "cta", "Lasst gerne ein") == [{"text": "Abonnieren"}]


def test_name_stickers_only_without_product_cards() -> None:
    assert not _graphic(_brief(), "product_sticker", "Alpha Rakete")
    assert _graphic(_brief(product_cards=False), "product_sticker", "Alpha Rakete") == [
        {"image": "img_a"}
    ]


def test_every_say_phrase_is_unique_in_the_speech() -> None:
    said = [" ".join(t.casefold().split()) for _, t in SPEECH]
    for g in _brief().graphics:
        phrase = " ".join(g.say.casefold().split())
        assert sum(s.count(phrase) for s in said) == 1, g.say
        assert g.source.startswith("auto brief: ")


def test_cover_footage_follows_the_words_of_its_file_name() -> None:
    brief = _brief()
    cover = {(c.footage, c.over, c.start, c.end, c.footage_start, c.seconds) for c in brief.cover}
    assert cover == {
        # the series spreads over the sentences of one recording; two clips share the first
        ("gag0", "cam1", 0.0, 1.5, 0.5, 1.5),
        ("gag1", "cam1", 1.5, 3.0, 0.0, 1.5),
        ("gag2", "cam1", 3.0, 4.8, 0.0, 1.8),
        # the archive clip starts where its word ("Ansturm") is said, in its longest shot
        ("crowd", "cam1", 4.8, 6.0, 10.5, 1.2),
        # the screen walkthrough covers the recording's sentences, past the recorder's shot
        ("scr", "cam2", 0.0, 6.0, 3.0, 6.0),
    }
    assert brief.unused == ("proj_reaktionen",)


def test_static_bands_crop_browser_bar_and_taskbar() -> None:
    items = (
        [ScreenText(text="12:00", shot=s, y0=0.95, y1=0.98) for s in range(5)]
        + [  # the URL bar from the second shot on (the first is the recorder's window)
            ScreenText(text="shop.de/angebote", shot=s, y0=0.05, y1=0.09) for s in range(1, 5)
        ]
        + [  # page text scrolls
            ScreenText(text="Angebot der Woche", shot=s, y0=0.5 - s / 10, y1=0.55 - s / 10)
            for s in range(5)
        ]
    )
    assert static_bands(items) == (0.0, 0.1, 1.0, 0.84)
    assert static_bands(items[:2]) is None  # too few shots to tell
    brief = _brief(screen_text={"scr": tuple(items)})
    assert [(w.asset_id, w.window) for w in brief.screens] == [("scr", (0.0, 0.1, 1.0, 0.84))]


def test_channel_clips_background_and_format_defaults() -> None:
    brief = _brief()
    assert (brief.intro and brief.intro.asset_id, brief.outro and brief.outro.asset_id) == (
        "intro",
        "outro",
    )
    assert brief.background is not None and brief.background.asset_id == "bg"
    assert (brief.style.screen_style, brief.style.focus_blur) == ("card", 5.0)
    assert brief.style.vfx_overrides == (("punch_in.apply", "never"),)
    generic = _brief(format_profile="generic").style
    assert (generic.screen_style, generic.focus_blur, generic.vfx_overrides) == ("crop", 0.0, ())


def test_same_inputs_same_brief() -> None:
    assert _brief() == _brief()


def test_hand_entries_win_over_the_automatic_ones() -> None:
    brief = _brief()
    own = (GraphicWish(kind="cta", say="Lasst gerne"),)
    settings = StageSettings(graphic_wishes=own, vfx_overrides=(("punch_in.apply", "always"),))
    merged = apply_auto_brief(settings, brief, frozenset({"graphics", "vfx_overrides"}))
    assert merged.graphic_wishes == own
    assert merged.vfx_overrides == (("punch_in.apply", "always"),)
    assert merged.screen_style == "card" and merged.focus_blur == 5.0
    assert merged.intro_asset_id == "intro" and merged.background_footage == "bg"
    assert {c[0] for c in merged.cover_wishes} == {"gag0", "gag1", "gag2", "crowd", "scr"}

    auto = apply_auto_brief(StageSettings(), brief, frozenset())
    assert len(auto.graphic_wishes) == len(brief.graphics)
    assert all(w.reason.startswith("auto brief: ") for w in auto.graphic_wishes)
    assert auto.vfx_overrides == (("punch_in.apply", "never"),)
