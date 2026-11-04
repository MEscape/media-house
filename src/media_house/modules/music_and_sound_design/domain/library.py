"""The owner's sound library as this module sees it: manifest entries, licences, search.

The library is the ONLY source of audio. Nothing here downloads, generates or substitutes a
sound: a need the library cannot meet becomes a missing-asset recommendation in the plan.

Facts a source states (mood, genre, BPM, description) are kept apart from what was measured
(``MusicAnalysis`` / ``SfxProfile``). Selection reads both, labelled.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from media_house.modules.music_and_sound_design.domain.analysis import (
    MusicAnalysis,
    SfxProfile,
)
from media_house.modules.music_and_sound_design.domain.values import (
    CATEGORY_TAG_PREFIX,
    KIND_TAG_PREFIX,
    LIBRARY_TAG,
    SOURCE_TAG_PREFIX,
    LicenceState,
    SoundKind,
)

_TAG_MAX = 50
_TAG_BAD = re.compile(r"[,\s]+")


@dataclass(frozen=True, slots=True, kw_only=True)
class Licence:
    name: str
    url: str
    #: ``None`` = the source neither allows nor forbids commercial use: needs the owner.
    commercial_use: bool | None
    attribution_required: bool
    #: The exact credit text, used verbatim in the video description.
    attribution_text: str | None
    restrictions: str | None = None
    #: ``False`` = the file must never be used as model or test data.
    ai_training_allowed: bool | None = None
    author: str = ""
    source: str = ""
    source_url: str = ""
    note: str | None = None

    @property
    def state(self) -> LicenceState:
        if self.commercial_use is True:
            return LicenceState.CLEARED
        if self.commercial_use is None:
            return LicenceState.UNCONFIRMED
        return LicenceState.NOT_COMMERCIAL


@dataclass(frozen=True, slots=True, kw_only=True)
class StatedMusic:
    """What the music's source says about it (never measured, never invented)."""

    bpm: float | None = None
    moods: tuple[str, ...] = ()
    genres: tuple[str, ...] = ()
    energy: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ManifestEntry:
    """One file of the owner's manifest (validated by the infrastructure reader)."""

    path: str
    kind: SoundKind
    title: str
    categories: tuple[str, ...]
    tags: tuple[str, ...]
    licence: Licence
    sha256: str
    duration_seconds: float
    sample_rate: int
    channels: int
    stated: StatedMusic | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SoundManifest:
    vocabulary: tuple[str, ...]
    entries: tuple[ManifestEntry, ...]
    description: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class LicencePolicy:
    """Which licence states may be selected. Default: only ``commercial_use: true``."""

    allow_unconfirmed: bool = False
    allow_not_commercial: bool = False

    def allows(self, licence: Licence) -> bool:
        state = licence.state
        return (
            state is LicenceState.CLEARED
            or (state is LicenceState.UNCONFIRMED and self.allow_unconfirmed)
            or (state is LicenceState.NOT_COMMERCIAL and self.allow_not_commercial)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SoundAsset:
    """A library sound with its manifest facts and (when stored) its measurement."""

    asset_id: str
    kind: SoundKind
    title: str
    categories: tuple[str, ...]
    tags: tuple[str, ...]
    duration_seconds: float
    checksum: str
    licence: Licence
    stated: StatedMusic | None = None
    music: MusicAnalysis | None = None
    sfx: SfxProfile | None = None
    analysis_asset_id: str | None = None
    favorite: bool = False

    @property
    def measured(self) -> bool:
        return self.music is not None or self.sfx is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class SoundQuery:
    kind: SoundKind | None = None
    #: Any of these categories qualifies; earlier ones rank higher.
    categories: tuple[str, ...] = ()
    #: Free words matched against title, tags, categories and stated moods/genres.
    text: str = ""
    min_seconds: float | None = None
    max_seconds: float | None = None
    policy: LicencePolicy = field(default_factory=LicencePolicy)
    #: ``True`` keeps only sounds with a stored measurement.
    measured_only: bool = False
    exclude: frozenset[str] = frozenset()
    limit: int = 50


@dataclass(frozen=True, slots=True, kw_only=True)
class SoundMatch:
    asset: SoundAsset
    score: float
    reasons: tuple[str, ...]


def library_tags(entry: ManifestEntry) -> tuple[str, ...]:
    """The controlled tags of an imported sound (library tags are filters, so few and exact)."""
    tags = [
        LIBRARY_TAG,
        KIND_TAG_PREFIX + entry.kind.value,
        *(CATEGORY_TAG_PREFIX + c for c in entry.categories),
    ]
    if entry.licence.source:
        tags.append(SOURCE_TAG_PREFIX + entry.licence.source)
    return tuple(t for t in (_tag(t) for t in tags) if t)


def _tag(text: str) -> str:
    return _TAG_BAD.sub("_", text.strip().casefold())[:_TAG_MAX]


def entry_metadata(entry: ManifestEntry, manifest_path: str) -> dict[str, object]:
    """The manifest facts as stored in the asset metadata (JSON-compatible)."""
    lic = entry.licence
    stated = entry.stated
    return {
        "kind": entry.kind.value,
        "title": entry.title,
        "categories": list(entry.categories),
        "tags": list(entry.tags),
        "manifest_path": manifest_path,
        "manifest_sha256": entry.sha256,
        "licence": {
            "name": lic.name,
            "url": lic.url,
            "commercial_use": lic.commercial_use,
            "attribution_required": lic.attribution_required,
            "attribution_text": lic.attribution_text,
            "restrictions": lic.restrictions,
            "ai_training_allowed": lic.ai_training_allowed,
            "author": lic.author,
            "source": lic.source,
            "source_url": lic.source_url,
            "note": lic.note,
        },
        "stated": None
        if stated is None
        else {
            "bpm": stated.bpm,
            "moods": list(stated.moods),
            "genres": list(stated.genres),
            "energy": stated.energy,
            "description": stated.description,
        },
    }


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _texts(value: object) -> tuple[str, ...]:
    return tuple(v for v in value if isinstance(v, str)) if isinstance(value, list) else ()


def sound_from_metadata(
    *,
    asset_id: str,
    checksum: str,
    duration_seconds: float | None,
    favorite: bool,
    metadata: Mapping[str, object],
) -> SoundAsset | None:
    """The library sound described by an asset's ``sound_library`` metadata, else ``None``."""
    kind_text = metadata.get("kind")
    if kind_text not in {k.value for k in SoundKind}:
        return None
    raw_lic = metadata.get("licence")
    lic: Mapping[str, object] = raw_lic if isinstance(raw_lic, Mapping) else {}
    commercial = lic.get("commercial_use")
    ai = lic.get("ai_training_allowed")
    licence = Licence(
        name=_text(lic.get("name")) or "",
        url=_text(lic.get("url")) or "",
        commercial_use=commercial if isinstance(commercial, bool) else None,
        attribution_required=lic.get("attribution_required") is True,
        attribution_text=_text(lic.get("attribution_text")),
        restrictions=_text(lic.get("restrictions")),
        ai_training_allowed=ai if isinstance(ai, bool) else None,
        author=_text(lic.get("author")) or "",
        source=_text(lic.get("source")) or "",
        source_url=_text(lic.get("source_url")) or "",
        note=_text(lic.get("note")),
    )
    raw_stated = metadata.get("stated")
    stated = None
    if isinstance(raw_stated, Mapping):
        bpm = raw_stated.get("bpm")
        stated = StatedMusic(
            bpm=float(bpm) if isinstance(bpm, int | float) and not isinstance(bpm, bool) else None,
            moods=_texts(raw_stated.get("moods")),
            genres=_texts(raw_stated.get("genres")),
            energy=_text(raw_stated.get("energy")),
            description=_text(raw_stated.get("description")),
        )
    return SoundAsset(
        asset_id=asset_id,
        kind=SoundKind(str(kind_text)),
        title=_text(metadata.get("title")) or asset_id,
        categories=_texts(metadata.get("categories")),
        tags=_texts(metadata.get("tags")),
        duration_seconds=float(duration_seconds or 0.0),
        checksum=checksum,
        licence=licence,
        stated=stated,
        favorite=favorite,
    )


class SoundIndex:
    """An in-memory, searchable view of the library (built per planning run; nothing stored)."""

    def __init__(self, sounds: Iterable[SoundAsset]) -> None:
        self._sounds = tuple(sorted(sounds, key=lambda s: s.asset_id))
        self._by_id = {s.asset_id: s for s in self._sounds}

    def __len__(self) -> int:
        return len(self._sounds)

    @property
    def sounds(self) -> tuple[SoundAsset, ...]:
        return self._sounds

    def get(self, asset_id: str) -> SoundAsset | None:
        return self._by_id.get(asset_id)

    def fingerprint(self) -> str:
        """Changes when a sound, its manifest facts or its measurement change."""
        rows = [
            [
                s.asset_id,
                s.checksum,
                s.categories,
                s.licence.commercial_use,
                s.licence.attribution_text,
                s.analysis_asset_id,
                s.favorite,
            ]
            for s in self._sounds
        ]
        return hashlib.sha256(json.dumps(rows, default=str).encode("utf-8")).hexdigest()

    def search(self, query: SoundQuery) -> list[SoundMatch]:
        matches = []
        words = [w for w in re.split(r"\W+", query.text.casefold()) if w]
        for sound in self._sounds:
            if query.kind is not None and sound.kind is not query.kind:
                continue
            if sound.asset_id in query.exclude or not query.policy.allows(sound.licence):
                continue
            if query.measured_only and not sound.measured:
                continue
            seconds = sound.duration_seconds
            if query.min_seconds is not None and seconds < query.min_seconds:
                continue
            if query.max_seconds is not None and seconds > query.max_seconds:
                continue
            scored = relevance(sound, query.categories, words)
            if scored is None:
                continue
            matches.append(SoundMatch(asset=sound, score=scored[0], reasons=scored[1]))
        matches.sort(key=lambda m: (-m.score, m.asset.asset_id))
        return matches[: query.limit]


def relevance(
    sound: SoundAsset, categories: tuple[str, ...], words: list[str]
) -> tuple[float, tuple[str, ...]] | None:
    """0..1 relevance of a sound to the asked categories and words; ``None`` = not relevant.

    A category match outweighs words (the categories are the owner's controlled vocabulary);
    the first listed category is the preferred one. Measured sounds and favourites rank a
    little higher at equal relevance.
    """
    reasons: list[str] = []
    score = 0.0
    if categories:
        hits = [i for i, c in enumerate(categories) if c in sound.categories]
        if not hits:
            return None
        score += 0.7 * (1.0 - 0.15 * min(hits[0], 4))
        reasons.append(f"category {categories[hits[0]]}")
    if words:
        haystack = " ".join(
            [
                sound.title,
                *sound.tags,
                *sound.categories,
                *(sound.stated.moods if sound.stated else ()),
                *(sound.stated.genres if sound.stated else ()),
            ]
        ).casefold()
        found = [w for w in words if w in haystack]
        if not found and not categories:
            return None
        score += 0.2 * len(found) / len(words)
        if found:
            reasons.append("matches " + ", ".join(found))
    elif not categories:
        score += 0.2
    if sound.measured:
        score += 0.05
        reasons.append("measured")
    if sound.favorite:
        score += 0.05
        reasons.append("favourite")
    return round(min(score, 1.0), 4), tuple(reasons)


def alternatives_by_title(entries: Iterable[ManifestEntry]) -> list[tuple[str, str]]:
    """Pairs ``(path, path)`` whose titles differ only by a version suffix ("... faster")."""

    def stem(title: str) -> str:
        base = re.sub(r"\(.*?\)|\[.*?\]", " ", title.casefold())
        return " ".join(re.findall(r"[a-z0-9]+", base))

    seen: dict[str, str] = {}
    pairs = []
    ordered = sorted(entries, key=lambda e: (len(stem(e.title)), e.path))
    for entry in ordered:
        key = stem(entry.title)
        if not key:
            continue
        for other_key, other_path in seen.items():
            if key != other_key and key.startswith(other_key + " ") and len(other_key) >= 6:
                pairs.append((other_path, entry.path))
        seen.setdefault(key, entry.path)
    return pairs
