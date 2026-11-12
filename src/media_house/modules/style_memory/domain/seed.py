"""The cold start: the channel's style as the evidence documents recorded it, and what the first
manual session taught. Every statement names where it comes from (``docs/layer4`` files, the
video ids of ``channel-evidence.json``, or the session).

The look (colour, type, graphic language) is owned by ``visual_style_engine``; this seed holds the
editorial side only: voice, hooks, structure, pacing, allowed and forbidden devices.
"""

from datetime import datetime

from media_house.modules.style_memory.domain.model import (
    CaptionPolicy,
    FormatProfile,
    HookPattern,
    PacingNorm,
    PreferenceEvent,
    Provenance,
    StyleProfile,
    StyleRule,
)
from media_house.modules.style_memory.domain.rulebook import write_rule
from media_house.modules.style_memory.domain.values import (
    Category,
    EventKind,
    Scope,
    Strength,
)

SEED_VERSION = 1
_DISCOVERY = "file:docs/layer4/channel-discovery.md"
_IDENTITY = "file:docs/layer4/visual-identity.md"
_EVIDENCE = "file:docs/layer4/channel-evidence.json"


def _video(video_id: str, quote: str = "") -> Provenance:
    return Provenance(f"video:{video_id}", quote)


def mesc_profile(now: datetime) -> StyleProfile:
    ranking = (_video("t_BTkcPy5sw"), _video("d2ChnPz3FRg"), _video("bw7fkdSK1Vk"))
    brochure = (_video("9etWVDZnyOs"),)
    return StyleProfile(
        version=SEED_VERSION,
        channel="MEsc",
        language="de",
        audience=(
            "Deutsche Feuerwerks-Fans und Silvester-Käufer, die vor dem Kauf Preise, "
            "Verfügbarkeit und den Effekt vergleichen wollen: klares Urteil, Preis, Wo kaufen, "
            "Wirkungsbeweis."
        ),
        address="Informelles Deutsch. Zuschauer werden mit „meine Freunde“ angesprochen.",
        greeting="Was geht ab Pyros",
        catchphrases=("abonnieren, liken und Schwarzpulver inhalieren",),
        hook_patterns=(
            HookPattern(
                "claim_hook",
                "Ein klares Versprechen in einem Satz (ca. 25 s): „Diese zehn Feuerwerksartikel "
                "dürfen an Silvester auf keinen Fall fehlen“.",
                ranking,
            ),
            HookPattern(
                "release_day_announcement",
                "Die Neuigkeit zuerst, direkt und aufgeregt: „Das Prospekt ist da, meine Freunde“.",
                brochure,
            ),
        ),
        pacing=(
            PacingNorm("ranking", 4.0, 6.0, 3.0, 146.0, ranking),
            PacingNorm("brochure_review", 5.0, 6.5, 2.0, 172.0, brochure),
            PacingNorm(
                "generic", 4.0, 7.0, 2.0, 140.0, (Provenance(_IDENTITY, "4-7 cut events/min"),)
            ),
        ),
        allowed_transitions=(
            "hard_cut",
            "jump_cut",
            "cross_dissolve",
            "whip_pan",
            "flash",
            "slide",
            "zoom_tunnel",
            "pixel_dissolve",
        ),
        forbidden_transitions=("glitch", "shape", "mask_reveal", "wipe"),
        overlay_limits=(("default", 2), ("ranking", 3), ("uncut", 1)),
        caption_policy=CaptionPolicy(
            burned_in=False,
            note="Die Lieblingsvideos haben keine eingebrannten Untertitel; nur das Werbung-Label.",
            evidence=(Provenance(_DISCOVERY, "10 Remaining uncertainties"),),
        ),
        recurring_layouts=(
            "Gesichtskamera als Bild-im-Bild oben links, Prospektseite rechts",
            "Produkt-Freisteller mit weißem Glow über Nachthimmel oder Feuerplatte",
            "Feuer-Endscreen (ca. 20 s) mit Pixel-Auflösung",
            "Zoom-Tunnel in den Mitglieder-Hinweis in den ersten 30 s",
        ),
        never=(
            "Zukunftsversprechen oder Superlative ohne Beleg",
            "Dezimalpunkt bei Preisen (15.99) oder fehlendes Euro-Zeichen",
            "Künstliches Feuerwerk über echtem Testmaterial",
            "Dekorative Übergänge (Glitch, Form, Maske)",
        ),
        formats=(
            FormatProfile(
                "ranking",
                "Ranking / Top-N",
                (
                    "Claim-Hook",
                    "Mitglieder-Hinweis",
                    "Countdown Platz N bis Platz 1",
                    "Rückblick",
                    "Kommentar-Frage",
                    "Catchphrase",
                    "Endscreen",
                ),
                ("klares Urteil", "Preis", "Verfügbarkeit", "Effekt-Beweis"),
                9.0,
                11.5,
                ranking,
            ),
            FormatProfile(
                "brochure_review",
                "Prospekt-Besprechung",
                (
                    "Neuigkeit zuerst",
                    "Seite für Seite mit Preis-Kommentar",
                    "Regionale Verfügbarkeit",
                    "Meinungs-Frage",
                    "Catchphrase",
                    "Endscreen",
                ),
                ("vollständig", "schnell", "korrekte Preise am Erscheinungstag"),
                8.0,
                12.0,
                brochure,
            ),
            FormatProfile(
                "generic",
                "Allgemein",
                ("Hook", "Hauptteil", "Fazit", "Catchphrase", "Endscreen"),
                ("klare Information",),
                4.0,
                12.0,
            ),
        ),
        visual_identity="visual_style_engine",
        updated_at=now,
        provenance=(Provenance(_DISCOVERY), Provenance(_IDENTITY), Provenance(_EVIDENCE)),
    )


def mesc_rules(now: datetime) -> tuple[StyleRule, ...]:
    def rule(
        rule_id: str,
        text: str,
        category: Category,
        strength: Strength,
        source: str,
        quote: str = "",
    ) -> StyleRule:
        return write_rule(
            rule_id=f"seed.{rule_id}",
            text=text,
            category=category,
            strength=strength,
            scope=Scope.GLOBAL,
            scope_key="",
            now=now,
            evidence=(Provenance(source, quote),),
        )

    return (
        rule(
            "voice.spoken_short",
            "Kurze gesprochene Sätze im Ton des Kanals, Zuschauer als „meine Freunde“. "
            "Kein Schreibdeutsch, kein Selbst-Pitch.",
            Category.VOICE,
            Strength.DEFAULT,
            _DISCOVERY,
            "8 Recurring: 'meine Freunde'",
        ),
        rule(
            "voice.scene_names_plain",
            "Szenen heißen nach ihrer Funktion in einfachen Worten (Einstieg, Zahlen, "
            "Vergleich), nie nach einem Motto.",
            Category.STRUCTURE,
            Strength.DEFAULT,
            "session:manual",
            "„Das Versprechen“ hat verwirrt",
        ),
        rule(
            "hook.first_five_seconds",
            "Ein Hook in den ersten 5 Sekunden.",
            Category.HOOK,
            Strength.DEFAULT,
            _EVIDENCE,
            "recurring_elements: claim hook",
        ),
        rule(
            "structure.outro_catchphrase",
            "Der Abschluss trägt die Catchphrase des Kanals, vor dem Endscreen.",
            Category.STRUCTURE,
            Strength.DEFAULT,
            _EVIDENCE,
            "outro catchphrase in 8 samples",
        ),
        rule(
            "assets.raw_footage_only",
            "Vom Creator werden nur Rohmaterial (Video, Bilder, Screenaufnahmen) angefragt, "
            "keine Musik und keine Soundeffekte: Ton, Musik und Schnitt übernimmt die Engine.",
            Category.ASSETS,
            Strength.MANDATORY,
            "spec:brief-to-cut",
            "Non-goals",
        ),
        rule(
            "never.thumbnails",
            "Keine Thumbnails planen, erzeugen oder abfragen.",
            Category.NEVER,
            Strength.MANDATORY,
            "spec:brief-to-cut",
            "Non-goals: NO THUMBNAILS",
        ),
        rule(
            "never.unsupported_superlatives",
            "Keine Superlative ohne Beleg: nur „unter diesen fünf“, nie „von allen“.",
            Category.NEVER,
            Strength.MANDATORY,
            "spec:brief-to-cut",
            "Quality bar 2: honest pushback",
        ),
        rule(
            "graphics.decimal_comma",
            "Preise mit Dezimalkomma und Euro-Zeichen (15,99 €).",
            Category.GRAPHICS,
            Strength.MANDATORY,
            _IDENTITY,
            "dot decimal prices prohibited",
        ),
        rule(
            "transition.no_decorative",
            "Keine dekorativen Übergänge (Glitch, Form, Maske); Zoom-Tunnel und "
            "Pixel-Auflösung nur als Klammer am Anfang und Ende.",
            Category.TRANSITION,
            Strength.MANDATORY,
            _IDENTITY,
            "transitions.prohibited / bookend_only",
        ),
        rule(
            "pacing.hard_cuts_dominate",
            "Harte Schnitte dominieren (4-7 Schnittereignisse pro Minute).",
            Category.PACING,
            Strength.DEFAULT,
            _DISCOVERY,
            "5 Per-format characteristics",
        ),
    )


def manual_session_events(now: datetime) -> tuple[PreferenceEvent, ...]:
    """What the first manual session taught, as the creator's own instructions."""

    def event(
        signal: str,
        note: str,
        category: Category,
        *,
        scope: Scope = Scope.GLOBAL,
        client: str = "",
    ) -> PreferenceEvent:
        return PreferenceEvent(
            event_id=f"seed.session.{signal}",
            kind=EventKind.REGENERATE_INSTRUCTION,
            at=now,
            project_id="seed:manual-session",
            scene_id="",
            rule_id="",
            format="ranking",
            client=client,
            category=category,
            signal=signal,
            note=note,
            scope_hint=scope,
        )

    return (
        event(
            "alltags_ritual_als_aufhaenger",
            "Der Wecker bzw. ein Alltags-Ritual ist der Aufhänger statt eines generischen Intros.",
            Category.HOOK,
        ),
        event(
            "technische_tiefe",
            "Technische Tiefe zeigen: Aufbau, Kaliber, Effekt, nicht nur Zahlen vorlesen.",
            Category.TECHNICAL,
        ),
        event(
            "vergleiche_nur_im_angebot",
            "Vergleiche nur innerhalb des Angebots, nie mit anderen Anbietern.",
            Category.NEVER,
        ),
        event(
            "weniger_dateien",
            "Weniger Dateien: ein durchgehender Take statt vieler kleiner.",
            Category.ASSETS,
        ),
        event(
            "intro_fliesst_aus_kontext",
            "Das Intro muss aus dem Kontext fließen, kein Selbst-Pitch.",
            Category.VOICE,
        ),
        event(
            "humor_gag_mit_callback",
            "Der Humor-Gag mit Callback bleibt.",
            Category.HUMOR,
        ),
        event(
            "alte_prospekte_logos_nicht_abdecken",
            "Hersteller-Logos auf alten Prospekten müssen nicht abgedeckt werden.",
            Category.ASSETS,
            scope=Scope.CLIENT,
            client="Lidl",
        ),
    )
