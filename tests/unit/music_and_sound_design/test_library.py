"""Manifest validation, library tags/metadata, search relevance and licence filtering."""

import json
from pathlib import Path

import pytest

from media_house.modules.music_and_sound_design.domain.errors import InvalidManifest
from media_house.modules.music_and_sound_design.domain.library import (
    LicencePolicy,
    SoundIndex,
    SoundQuery,
    alternatives_by_title,
    entry_metadata,
    library_tags,
    sound_from_metadata,
)
from media_house.modules.music_and_sound_design.domain.values import LicenceState, SoundKind
from media_house.modules.music_and_sound_design.infrastructure.manifest import JsonManifestReader
from tests.support.sound_fakes import music, sfx


def _file(path: str, kind: str = "sfx", **changes: object) -> dict[str, object]:
    return {
        "path": path,
        "kind": kind,
        "source": "kenney",
        "source_page_url": "https://example.invalid/pack",
        "licence_name": "CC0",
        "licence_url": "https://example.invalid/cc0",
        "commercial_use": True,
        "attribution_required": False,
        "attribution_text": None,
        "author": "someone",
        "original_title": path.rsplit("/", 1)[-1],
        "tags": ["pack"],
        "categories": ["pop"],
        "duration_seconds": 0.4,
        "sample_rate": 44100,
        "channels": 1,
        "sha256": "a" * 64,
        "an_extra_owner_note": "kept",
        **changes,
    }


def _write(tmp_path: Path, files: list[dict[str, object]]) -> Path:
    path = tmp_path / "manifest.json"
    vocabulary = ["pop", "hype_energetic", "impact"]
    path.write_text(json.dumps({"category_vocabulary": vocabulary, "files": files}), "utf-8")
    return path


def test_the_manifest_is_read_with_licences_and_stated_facts(tmp_path: Path) -> None:
    track = _file(
        "music/x/Track.mp3",
        "music",
        categories=["hype_energetic"],
        commercial_use=None,
        commercial_use_note="ask the artist",
        attribution_required=True,
        attribution_text='"Track" by X',
        bpm_source_stated="122",
        mood={"value": ["Bright", "Driving"], "provenance": "source_stated"},
        genre={"value": ["Electronica"], "provenance": "source_stated"},
        energy={"value": None, "provenance": "source_stated"},
        ai_training_allowed=False,
    )
    manifest = JsonManifestReader().read(_write(tmp_path, [_file("sfx/pop.ogg"), track]))

    pop, song = manifest.entries
    assert pop.kind is SoundKind.SFX and pop.stated is None
    assert song.licence.state is LicenceState.UNCONFIRMED
    assert song.licence.attribution_text == '"Track" by X'
    assert song.licence.ai_training_allowed is False
    assert song.stated is not None
    assert song.stated.bpm == 122.0 and song.stated.moods == ("Bright", "Driving")
    assert song.stated.energy is None


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "ambience"},
        {"sha256": "nothex"},
        {"categories": []},
        {"categories": ["unknown_category"]},
        {"path": "../outside.wav"},
        {"commercial_use": "yes"},
    ],
)
def test_an_invalid_manifest_is_refused(tmp_path: Path, bad: dict[str, object]) -> None:
    with pytest.raises(InvalidManifest):
        JsonManifestReader().read(_write(tmp_path, [_file("sfx/pop.ogg") | bad]))


def test_tags_and_metadata_round_trip_through_the_asset(tmp_path: Path) -> None:
    entry = JsonManifestReader().read(_write(tmp_path, [_file("sfx/Big Pop.ogg")])).entries[0]

    tags = library_tags(entry)
    meta = entry_metadata(entry, entry.path)
    sound = sound_from_metadata(
        asset_id="a1", checksum="a" * 64, duration_seconds=0.4, favorite=True, metadata=meta
    )

    assert tags == ("sound_library", "sound:sfx", "category:pop", "source:kenney")
    assert sound is not None
    assert sound.categories == ("pop",) and sound.licence == entry.licence
    assert sound.favorite
    assert (
        sound_from_metadata(
            asset_id="x", checksum="", duration_seconds=None, favorite=False, metadata={}
        )
        is None
    )


def test_search_ranks_by_category_order_and_respects_the_licence_policy() -> None:
    index = SoundIndex(
        [
            sfx("hit", ("impact",)),
            sfx("pop", ("pop",)),
            sfx("unsure", ("impact",), commercial=None),
            music("song", ("hype_energetic",)),
        ]
    )

    found = index.search(SoundQuery(kind=SoundKind.SFX, categories=("pop", "impact")))
    loose = index.search(
        SoundQuery(categories=("impact",), policy=LicencePolicy(allow_unconfirmed=True))
    )

    assert [m.asset.asset_id for m in found] == ["pop", "hit"]
    assert found[0].score > found[1].score
    assert {m.asset.asset_id for m in loose} == {"hit", "unsure"}
    assert index.search(SoundQuery(kind=SoundKind.SFX, max_seconds=0.1)) == []
    assert index.fingerprint() != SoundIndex([sfx("hit", ("impact",))]).fingerprint()


def test_alternative_versions_are_detected_by_title(tmp_path: Path) -> None:
    files = [
        _file("music/a.mp3", "music", original_title="Scheming Weasel", sha256="b" * 64,
              categories=["hype_energetic"]),
        _file("music/b.mp3", "music", original_title="Scheming Weasel (faster)", sha256="c" * 64,
              categories=["hype_energetic"]),
        _file("music/c.mp3", "music", original_title="Other", sha256="d" * 64,
              categories=["hype_energetic"]),
    ]  # fmt: skip
    entries = JsonManifestReader().read(_write(tmp_path, files)).entries

    assert alternatives_by_title(entries) == []  # "(faster)" is stripped: same stem, not a pair
    renamed = [_file("music/d.mp3", "music", original_title="Scheming Weasel faster",
                     sha256="e" * 64, categories=["hype_energetic"]), files[0]]  # fmt: skip
    pairs = alternatives_by_title(JsonManifestReader().read(_write(tmp_path, renamed)).entries)
    assert pairs == [("music/a.mp3", "music/d.mp3")]
