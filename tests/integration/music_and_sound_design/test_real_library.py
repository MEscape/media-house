"""Opt-in: import and measure three files of the owner's REAL library.

Set ``MEDIA_HOUSE_SOUND_LIBRARY`` to the folder that holds ``manifest.json`` to run it. Files
flagged ``ai_training_allowed: false`` are never picked, and nothing is copied into the repo.
"""

import json
import os
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.modules.music_and_sound_design.application.contracts import (
    ImportSoundLibraryCommand,
    SoundKind,
    SoundLibraryCatalog,
    SoundLibraryImporter,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from tests.integration.music_and_sound_design.conftest import needs_ffmpeg

LIBRARY = os.environ.get("MEDIA_HOUSE_SOUND_LIBRARY")

pytestmark = [
    needs_ffmpeg,
    pytest.mark.skipif(not LIBRARY, reason="set MEDIA_HOUSE_SOUND_LIBRARY to run"),
]


def _pick(manifest: Path) -> tuple[str, ...]:
    files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    usable = [f for f in files if f.get("ai_training_allowed") is not False]
    track = next(f for f in usable if f["kind"] == "music" and f.get("bpm_source_stated"))
    effects = [f for f in usable if f["kind"] == "sfx"][:2]
    return (track["path"], *(f["path"] for f in effects))


def test_three_real_files_are_imported_measured_and_reused(app: Application) -> None:
    manifest = Path(str(LIBRARY)) / "manifest.json"
    command = ImportSoundLibraryCommand(manifest, only=_pick(manifest))
    importer = app.container.resolve(SoundLibraryImporter)

    first = importer.execute(command, JobContext.detached())
    second = importer.execute(command, JobContext.detached())

    assert isinstance(first, Ok), first
    assert isinstance(second, Ok), second
    assert len(first.value.analysis.analysed) == 3
    assert second.value.analysis.analysed == ()
    sounds = app.container.resolve(SoundLibraryCatalog).sounds()
    track = next(s for s in sounds if s.kind is SoundKind.MUSIC)
    assert track.music is not None and track.music.measurement.tempo is not None
    assert track.stated is not None and track.stated.bpm is not None
    ratio = track.music.measurement.tempo.bpm / track.stated.bpm
    assert min(abs(ratio - k) / k for k in (0.5, 1.0, 2.0)) < 0.04
    assert all(s.sfx is not None for s in sounds if s.kind is SoundKind.SFX)
