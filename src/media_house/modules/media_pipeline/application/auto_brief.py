"""The automatic run brief of a project folder, and how it meets the owner's hand brief.

``AutoBriefer`` reads what the library already holds (stored transcripts, video analyses,
inspections; nothing is analysed again) for every file of the project folder and lets
``domain.auto_brief`` decide graphics, cover footage, screen windows, background, intro/outro and
the format's style defaults. ``apply_auto_brief`` puts the result into the stage settings for
every brief key the owner did NOT write by hand: a hand entry always wins, a missing one is
filled automatically. Each decision carries its reason into the stored plans (graphic order
reasons, cover notes) and into ``AutoBriefResult.decisions`` for the run's log.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.layer4_inputs import GraphicWish
from media_house.modules.media_pipeline.domain.auto_brief import (
    AutoBrief,
    BriefInputs,
    Footage,
    Line,
    Product,
    ScreenText,
    auto_brief,
    role_of,
)
from media_house.modules.production_project.application.contracts import (
    TranscriptLine,
    conform,
    parse_script,
    sentence_lines,
)
from media_house.modules.story_architecture.application.contracts import StoryCatalog
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.modules.visual_style_engine.application.contracts import STORY_FORMAT_PROFILE
from media_house.shared.errors import Err

#: The brief keys (run config names) the automatic brief can fill.
AUTO_KEYS = frozenset(
    {
        "graphics",
        "cover",
        "screens",
        "background_footage",
        "intro",
        "outro",
        "screen_style",
        "focus_blur",
        "vfx_overrides",
    }
)
_PICTURES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_VIDEOS = frozenset({".mp4", ".mov", ".mxf", ".mkv", ".m4v", ".avi"})


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoBriefCommand:
    #: Every file of the project folder (its numbered sub-folders give each file its role).
    files: tuple[Path, ...]
    #: The recordings the video is cut from, in edit order (library asset ids).
    recordings: tuple[str, ...]
    #: The script (markdown) when there is one: scene headings and the video's title.
    script: str | None = None
    #: The run's settings (format, sponsorship, products, excluded speech).
    settings: StageSettings


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoBriefResult:
    brief: AutoBrief
    #: Library asset id -> file name, for logs.
    names: dict[str, str]
    #: Files the library would not take (path, why).
    skipped: tuple[tuple[str, str], ...] = ()


class AutoBriefer:
    def __init__(
        self,
        library: MediaLibrary,
        transcripts: AudioCatalog,
        videos: VideoCatalog,
        inspections: InspectionCatalog,
        stories: StoryCatalog,
    ) -> None:
        self._library = library
        self._transcripts = transcripts
        self._videos = videos
        self._inspections = inspections
        self._stories = stories

    def execute(self, command: AutoBriefCommand) -> AutoBriefResult:
        s = command.settings
        footage: list[Footage] = []
        screen_text: dict[str, tuple[ScreenText, ...]] = {}
        names: dict[str, str] = {}
        skipped: list[tuple[str, str]] = []
        for path in sorted(command.files):
            role = role_of(path.parent.name)
            suffix = path.suffix.casefold()
            if role is None or suffix not in _PICTURES | _VIDEOS:
                continue
            imported = self._library.import_file(path)  # stored once: a known file is found
            if isinstance(imported, Err):
                skipped.append((str(path), imported.error.user_message))
                continue
            asset_id = imported.value.asset.id
            names[asset_id] = path.name
            video = suffix in _VIDEOS
            duration, words = 0.0, 0
            shots: tuple[tuple[float, float], ...] = ()
            if video:
                duration, shots, text = self._picture(asset_id)
                words = len(self._words(asset_id))
                if text:
                    screen_text[asset_id] = text
            footage.append(
                Footage(
                    asset_id=asset_id,
                    name=path.stem,
                    role=role,
                    video=video,
                    duration=duration,
                    spoken_words=words,
                    shots=shots,
                )
            )
        brief = auto_brief(
            BriefInputs(
                format_profile=self._format(s, command.recordings),
                sponsored=s.sponsored,
                title=_title(command.script or ""),
                footage=tuple(footage),
                lines=self._lines(command.recordings, command.script, s.excluded_speech),
                products=_products(s),
                product_cards=bool(s.product_cards),
                screen_text=screen_text,
            )
        )
        return AutoBriefResult(brief=brief, names=names, skipped=tuple(skipped))

    # --- what the library already knows --------------------------------------------------------
    def _words(self, asset_id: str) -> list[tuple[str, float, float]]:
        stored = self._transcripts.latest(asset_id)
        if isinstance(stored, Err) or stored.value is None:
            return []
        return [(w.raw_word, w.start, w.end) for w in stored.value[0].transcript.words]

    def _picture(
        self, asset_id: str
    ) -> tuple[float, tuple[tuple[float, float], ...], tuple[ScreenText, ...]]:
        """Duration, shots and on-screen text of the stored video analysis; without one the
        stored inspection's duration (nothing is probed or analysed here)."""
        stored = self._videos.latest(asset_id)
        if not isinstance(stored, Err) and stored.value is not None:
            analysis = stored.value[0]
            shots = tuple((s.range.start.seconds, s.range.end.seconds) for s in analysis.shots)
            text = tuple(
                ScreenText(text=item.text, shot=shot.index, y0=item.box.y0, y1=item.box.y1)
                for shot in analysis.shots
                for item in shot.text.items
            )
            return analysis.source.duration_seconds or 0.0, shots, text
        inspected = self._inspections.find(asset_id)
        duration = inspected.inspection.observed.container.duration if inspected else None
        return duration or 0.0, (), ()

    def _lines(
        self,
        recordings: Sequence[str],
        script: str | None,
        excluded: Sequence[tuple[str, float, float, str]],
    ) -> tuple[Line, ...]:
        """The sentences of the recordings in edit order, without excluded speech; with a script
        each recording carries the heading of the scene most of its sentences speak."""
        lines: list[TranscriptLine] = []
        for asset_id in recordings:
            lines += sentence_lines(asset_id, self._words(asset_id))
        lines = [
            ln
            for ln in lines
            if not any(
                a == ln.asset_id and min(ln.end, b) - max(ln.start, s) > 0.5 * (ln.end - ln.start)
                for a, s, b, _ in excluded
            )
        ]
        scene: dict[str, str] = {}
        if script:
            parsed = parse_script(script)
            headings = {sc.scene_id: sc.heading for sc in parsed.scenes}
            votes: dict[str, dict[str, int]] = {}
            for m in conform(parsed, lines, list(recordings)).matches:
                if m.scene_id:
                    count = votes.setdefault(m.line.asset_id, {})
                    count[m.scene_id] = count.get(m.scene_id, 0) + 1
            for asset_id, count in votes.items():
                best = min(count, key=lambda sid: (-count[sid], sid))
                scene[asset_id] = headings.get(best, "")
        return tuple(
            Line(
                asset_id=ln.asset_id,
                start=ln.start,
                end=ln.end,
                text=ln.text,
                scene=scene.get(ln.asset_id, ""),
            )
            for ln in lines
        )

    def _format(self, s: StageSettings, recordings: Sequence[str]) -> str:
        """The run's format profile, else the one of its editing profile or story format, else
        the format of the stored story plan."""
        if s.format_profile:
            return s.format_profile
        for name in (s.edit_profile, s.story_format):
            if name and name in STORY_FORMAT_PROFILE:
                return STORY_FORMAT_PROFILE[name]
        if recordings:
            stored = self._stories.latest(recordings[0])
            if not isinstance(stored, Err) and stored.value is not None:
                return STORY_FORMAT_PROFILE.get(stored.value[0].target_format, "generic")
        return "generic"


def _title(script: str) -> str:
    """The script's first heading without its label ("Transcript: Lidl x Nico" -> "Lidl x Nico")."""
    for row in script.splitlines():
        if row.startswith("# "):
            return re.sub(r"^\w+\s*:\s*", "", row[2:].strip())
    return ""


def _products(s: StageSettings) -> tuple[Product, ...]:
    """The product table's products (cut-out and facts); without a table every named picture
    (a picture the table does not name, such as a brochure page, is no product)."""
    if s.product_cards:
        return tuple(Product(name=n, image=cut, facts=facts) for n, cut, facts in s.product_cards)
    return tuple(Product(name=n, image=image) for n, image in s.product_images)


def apply_auto_brief(
    settings: StageSettings, brief: AutoBrief, given: frozenset[str]
) -> StageSettings:
    """The automatic brief for every key of ``AUTO_KEYS`` the owner did not give (``given``);
    VFX overrides merge per path, the owner's winning."""
    changes: dict[str, object] = {}
    if "graphics" not in given:
        changes["graphic_wishes"] = tuple(
            GraphicWish(
                kind=g.kind,
                say=g.say,
                slots=tuple(sorted(g.slots)),
                variant=g.variant,
                offset=g.offset,
                duration=g.duration,
                zone=g.zone,
                emphasis=g.emphasis,
                reason=g.source,
            )
            for g in brief.graphics
        )
    if "cover" not in given:
        changes["cover_wishes"] = tuple(
            (c.footage, c.over, c.start, c.end, c.footage_start, c.seconds, c.source)
            for c in brief.cover
        )
    if "screens" not in given:
        changes["screen_windows"] = tuple(sorted((w.asset_id, *w.window) for w in brief.screens))
    for key, attribute, pick in (
        ("background_footage", "background_footage", brief.background),
        ("intro", "intro_asset_id", brief.intro),
        ("outro", "outro_asset_id", brief.outro),
    ):
        if key not in given and pick is not None:
            changes[attribute] = pick.asset_id
    if "screen_style" not in given:
        changes["screen_style"] = brief.style.screen_style
    if "focus_blur" not in given:
        changes["focus_blur"] = brief.style.focus_blur
    own = dict(settings.vfx_overrides)
    changes["vfx_overrides"] = tuple(sorted({**dict(brief.style.vfx_overrides), **own}.items()))
    return replace(settings, **changes)  # type: ignore[arg-type]


def decisions(result: AutoBriefResult, given: frozenset[str]) -> list[dict[str, object]]:
    """Every automatic decision with its reason (and whether a hand entry overrode it)."""
    b, name = result.brief, result.names
    out: list[dict[str, object]] = []

    def add(key: str, what: str, source: str) -> None:
        out.append({"key": key, "what": what, "source": source, "used": key not in given})

    for g in b.graphics:
        add("graphics", f"{g.kind}{':' + g.variant if g.variant else ''} over '{g.say}'", g.source)
    for c in b.cover:
        what = f"{name.get(c.footage, c.footage)} over {name.get(c.over, c.over)} "
        add("cover", what + f"{c.start:.1f}-{c.end:.1f}s", c.source)
    for w in b.screens:
        add("screens", f"{name.get(w.asset_id, w.asset_id)} window {w.window}", w.source)
    for key, pick in (("background_footage", b.background), ("intro", b.intro), ("outro", b.outro)):
        if pick is not None:
            add(key, name.get(pick.asset_id, pick.asset_id), pick.source)
    why = f"format {b.format_profile}: {b.style.why}"
    add("screen_style", b.style.screen_style, why)
    add("focus_blur", str(b.style.focus_blur), why)
    add("vfx_overrides", str(dict(b.style.vfx_overrides)), why)
    for unused in b.unused:
        out.append(
            {"key": "unused", "what": unused, "source": "no sentence names it", "used": False}
        )
    return out
