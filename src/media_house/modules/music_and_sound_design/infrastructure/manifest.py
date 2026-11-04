"""``ManifestReader`` adapter: the owner's ``manifest.json`` validated with pydantic.

The manifest is external data: unknown extra fields are tolerated (the owner may add notes),
but every field this module relies on is required and typed. A file outside the manifest's
folder or an unknown ``kind`` is refused.
"""

from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from media_house.modules.music_and_sound_design.domain.errors import InvalidManifest
from media_house.modules.music_and_sound_design.domain.library import (
    Licence,
    ManifestEntry,
    SoundManifest,
    StatedMusic,
)
from media_house.modules.music_and_sound_design.domain.values import SoundKind

_MAX_BYTES = 32 * 1024 * 1024


class _Stated(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True)
    value: list[str] | str | None = None
    provenance: str | None = None


class _File(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True)

    path: str = Field(min_length=1)
    kind: Literal["music", "sfx"]
    source: str = ""
    source_page_url: str = ""
    licence_name: str
    licence_url: str = ""
    commercial_use: bool | None
    commercial_use_note: str | None = None
    attribution_required: bool
    attribution_text: str | None = None
    licence_restrictions: str | None = None
    ai_training_allowed: bool | None = None
    author: str = ""
    original_title: str = ""
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(min_length=1)
    duration_seconds: float = Field(ge=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mood: _Stated | None = None
    genre: _Stated | None = None
    energy: _Stated | None = None
    bpm_source_stated: str | float | None = None
    description_source_stated: str | None = None

    @field_validator("path")
    @classmethod
    def _inside(cls, value: str) -> str:
        pure = PurePosixPath(value.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError("the path must stay inside the library folder")
        return pure.as_posix()


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True)
    description: str = ""
    category_vocabulary: list[str] = Field(default_factory=list)
    files: list[_File]


def _words(stated: _Stated | None) -> tuple[str, ...]:
    if stated is None or stated.value is None:
        return ()
    return (stated.value,) if isinstance(stated.value, str) else tuple(stated.value)


def _bpm(value: str | float | None) -> float | None:
    try:
        number = float(value) if value is not None else None
    except ValueError:
        return None
    return number if number and number > 0 else None


class JsonManifestReader:
    def read(self, path: Path) -> SoundManifest:
        try:
            if path.stat().st_size > _MAX_BYTES:
                raise InvalidManifest("the manifest is larger than 32 MiB")
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InvalidManifest(f"cannot read {path.name}: {exc.strerror}") from exc
        except UnicodeDecodeError as exc:
            raise InvalidManifest(f"{path.name} is not UTF-8") from exc
        try:
            raw = _Manifest.model_validate_json(text)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            raise InvalidManifest(f"{where}: {first['msg']}") from exc
        vocabulary = set(raw.category_vocabulary)
        entries = []
        for f in raw.files:
            unknown = [c for c in f.categories if vocabulary and c not in vocabulary]
            if unknown:
                raise InvalidManifest(f"{f.path}: categories {unknown} are not in the vocabulary")
            kind = SoundKind(f.kind)
            entries.append(
                ManifestEntry(
                    path=f.path,
                    kind=kind,
                    title=f.original_title or PurePosixPath(f.path).stem,
                    categories=tuple(f.categories),
                    tags=tuple(f.tags),
                    licence=Licence(
                        name=f.licence_name,
                        url=f.licence_url,
                        commercial_use=f.commercial_use,
                        attribution_required=f.attribution_required,
                        attribution_text=f.attribution_text,
                        restrictions=f.licence_restrictions,
                        ai_training_allowed=f.ai_training_allowed,
                        author=f.author,
                        source=f.source,
                        source_url=f.source_page_url,
                        note=f.commercial_use_note,
                    ),
                    sha256=f.sha256,
                    duration_seconds=f.duration_seconds,
                    sample_rate=f.sample_rate,
                    channels=f.channels,
                    stated=StatedMusic(
                        bpm=_bpm(f.bpm_source_stated),
                        moods=_words(f.mood),
                        genres=_words(f.genre),
                        energy=", ".join(_words(f.energy)) or None,
                        description=f.description_source_stated,
                    )
                    if kind is SoundKind.MUSIC
                    else None,
                )
            )
        return SoundManifest(
            vocabulary=tuple(raw.category_vocabulary),
            entries=tuple(entries),
            description=raw.description,
        )
