"""B-roll Intelligence against the real composition root and the real Media Library.

Real: the application container, the library (tiny MP4s imported through FFmpeg), the content
analyzer (from a supplied transcript), the video catalog, the shot index built from stored analyses,
the relationship catalog and the derived-document storage. Synthetic: what each clip "shows" (a
hand-made video analysis stored the way the analyzer stores its own), and the two embedding
encoders (fakes in a shared toy space). Nothing here says how good the recommendations are on real
footage or with the real CLIP/sentence models.
"""

import shutil
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.modules.broll_intelligence.application.contracts import (
    DOCUMENT_TYPE,
    AnalyzeBrollCommand,
    BrollAnalyzer,
    BrollCatalog,
    BrollConfig,
    LibraryScope,
    NothingToCover,
    Severity,
    Support,
)
from media_house.modules.content_intelligence.application.contracts import (
    AnalyzeContentCommand,
    ContentAnalyzer,
    ContentConfig,
    Profile,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.video_intelligence.application.contracts import ShotIndexCatalog
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.support.broll_library import (
    analyzer,
    footage_analysis,
    import_talk,
    import_video,
    stock,
    store_analysis,
)
from tests.support.ci_texts import lines_to_transcript
from tests.support.media_factories import make_wav

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required to import the test recordings",
    ),
]

POT = "a pot of water boiling on a stove while beans are added"


def test_the_whole_library_is_searched_and_the_right_footage_is_found(
    app: Application, tmp_path: Path
) -> None:
    talk = import_talk(app, tmp_path)
    clips = stock(app, tmp_path)

    result = analyzer(app).execute(
        AnalyzeBrollCommand((talk,), BrollConfig("generic")), JobContext.detached()
    )

    assert isinstance(result, Ok), result
    analysis = result.value.analysis
    assert analysis.issues_of(Severity.ERROR) == []
    assert analysis.pool.assets == 4 and analysis.pool.with_shots == 3
    assert [asset for asset, _ in analysis.pool.skipped] == [clips["raw"]]
    chosen = {r.concept: r.best for r in analysis.recommendations if r.best is not None}
    assert chosen, analysis.recommendations
    for best in chosen.values():
        if best.support is Support.SATISFIES:
            assert best.asset_id in (clips["pot"], clips["harbor"])
            assert best.asset_id != clips["cat"]


def test_the_result_is_stored_as_a_derived_document_and_read_back(
    app: Application, tmp_path: Path
) -> None:
    talk = import_talk(app, tmp_path)
    stock(app, tmp_path)
    run = analyzer(app)

    result = run.execute(AnalyzeBrollCommand((talk,)), JobContext.detached())

    assert isinstance(result, Ok), result
    assert result.value.created
    assert result.value.asset.metadata["processing_type"] == DOCUMENT_TYPE
    stored = app.container.resolve(BrollCatalog).latest(talk)
    assert isinstance(stored, Ok) and stored.value is not None
    assert stored.value[0] == result.value.analysis


def test_an_identical_rerun_reuses_everything_and_a_new_clip_makes_a_new_version(
    app: Application, tmp_path: Path
) -> None:
    talk = import_talk(app, tmp_path)
    stock(app, tmp_path)
    run = analyzer(app)
    first = run.execute(AnalyzeBrollCommand((talk,)), JobContext.detached())
    assert isinstance(first, Ok)

    again = run.execute(AnalyzeBrollCommand((talk,)), JobContext.detached())

    assert isinstance(again, Ok) and not again.value.created
    assert again.value.asset.id == first.value.asset.id

    newcomer = import_video(app, tmp_path / "clips", "kettle", 9)
    store_analysis(
        app, newcomer, footage_analysis(newcomer, description=POT, embedding="content:event")
    )
    updated = run.execute(AnalyzeBrollCommand((talk,)), JobContext.detached())

    assert isinstance(updated, Ok) and updated.value.created
    assert updated.value.analysis.analysis_version == 2
    assert updated.value.analysis.supersedes == first.value.asset.id
    assert updated.value.analysis.pool.with_shots == 4
    history = app.container.resolve(BrollCatalog).history(talk)
    assert len(history) == 2  # the first analysis is kept


def test_shot_indexes_are_built_once_and_not_parsed_again(app: Application, tmp_path: Path) -> None:
    talk = import_talk(app, tmp_path)
    clips = stock(app, tmp_path)
    library = app.container.resolve(MediaLibrary)
    catalog = app.container.resolve(ShotIndexCatalog)

    built = catalog.shot_index(clips["pot"])
    again = catalog.shot_index(clips["pot"])

    assert isinstance(built, Ok) and built.value is not None and built.value.built
    assert isinstance(again, Ok) and again.value is not None and not again.value.built
    assert again.value.asset.id == built.value.asset.id
    assert again.value.index.shots[0].description == POT
    indexes = [
        a
        for a in library.list_derived(clips["pot"])
        if a.metadata.get("processing_type") == "shot_index"
    ]
    assert len(indexes) == 1
    assert talk  # the talk recording was never needed for the index


def test_a_scope_limits_what_is_searched(app: Application, tmp_path: Path) -> None:
    talk = import_talk(app, tmp_path)
    clips = stock(app, tmp_path)

    result = analyzer(app).execute(
        AnalyzeBrollCommand((talk,), scope=LibraryScope(asset_ids=(clips["harbor"],))),
        JobContext.detached(),
    )

    assert isinstance(result, Ok), result
    assert result.value.analysis.pool.assets == 1
    for rec in result.value.analysis.recommendations:
        assert all(c.asset_id == clips["harbor"] for c in rec.candidates)


def test_an_unusable_asset_is_listed_not_silently_dropped(app: Application, tmp_path: Path) -> None:
    talk = import_talk(app, tmp_path)
    clips = stock(app, tmp_path)

    result = analyzer(app).execute(AnalyzeBrollCommand((talk,)), JobContext.detached())

    assert isinstance(result, Ok), result
    skipped = dict(result.value.analysis.pool.skipped)
    assert "no video analysis" in skipped[clips["raw"]]


def test_a_recording_with_nothing_to_say_is_refused_not_invented(
    app: Application, tmp_path: Path
) -> None:
    media = tmp_path / "audio"
    media.mkdir(exist_ok=True)
    imported = app.container.resolve(MediaLibrary).import_file(
        make_wav(media / "silent.wav", seconds=3)
    )
    assert isinstance(imported, Ok)
    asset_id = imported.value.asset.id
    analysed = app.container.resolve(ContentAnalyzer).execute(
        AnalyzeContentCommand(
            source_asset_id=asset_id,
            transcript=lines_to_transcript(["Hello there."], seconds_per_word=0.3),
            config=ContentConfig(profile=Profile.FAST),
        ),
        JobContext.detached(),
    )
    assert isinstance(analysed, Ok)

    result = analyzer(app).execute(AnalyzeBrollCommand((asset_id,)), JobContext.detached())

    assert isinstance(result, Err) and isinstance(result.error, NothingToCover)


def test_the_production_wiring_resolves_and_degrades_when_the_models_are_missing(
    app: Application, tmp_path: Path
) -> None:
    talk = import_talk(app, tmp_path)
    stock(app, tmp_path)

    result = app.container.resolve(BrollAnalyzer).execute(
        AnalyzeBrollCommand((talk,)), JobContext.detached()
    )

    assert isinstance(result, Ok), result
    stages = {s.stage: s.state.value for s in result.value.analysis.stages}
    assert stages["words"] == "ok"
    assert stages["pictures"] in ("ok", "not_available")
