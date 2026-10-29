"""Continuity Intelligence against the real composition root and the real Media Library.

Real: the container, the library (tiny MP4s through FFmpeg), the video catalog, derived-document
storage, the cache and history. Synthetic: what the clips "show" (video analyses with person tracks
stored the way the analyzer stores its own). Nothing here says how well continuity is judged on real
footage.
"""

import shutil
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.modules.continuity_intelligence.application.contracts import (
    DOCUMENT_TYPE,
    AnalyzeContinuityCommand,
    ContinuityAnalysis,
    ContinuityAnalyzer,
    ContinuityCatalog,
    ContinuityConfig,
    Decision,
    InvalidSequence,
    IssueDecision,
    IssueType,
    NothingToJudge,
    Severity,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.support.cont_library import (
    clip,
    import_clip,
    sequence,
    store_analysis,
    walking_analysis,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required to import the test recordings",
    ),
]


def reversal(app: Application, tmp_path: Path, **kw: object) -> tuple[str, ContinuityAnalyzer]:
    asset = import_clip(app, tmp_path / "clips", "walk")
    store_analysis(app, asset, walking_analysis(asset, **kw))  # type: ignore[arg-type]
    return asset.id, app.container.resolve(ContinuityAnalyzer)


def two_clips(asset: str, **kw: object):  # type: ignore[no-untyped-def]
    """A cut inside one recording: the walker goes right, then left."""
    return sequence(clip("c1", asset, 0.0, 2.0, 0.0), clip("c2", asset, 3.0, 4.0, 2.0), **kw)  # type: ignore[arg-type]


def run(analyzer: ContinuityAnalyzer, seq, profile: str = "cinematic_dialogue", **kw: object):  # type: ignore[no-untyped-def]
    return analyzer.execute(
        AnalyzeContinuityCommand(seq, ContinuityConfig(profile), **kw),  # type: ignore[arg-type]
        JobContext.detached(),
    )


def test_a_reversal_of_travel_in_the_real_library_is_found_explained_and_stored(
    app: Application, tmp_path: Path
) -> None:
    asset, analyzer = reversal(app, tmp_path)

    result = run(analyzer, two_clips(asset))

    assert isinstance(result, Ok), result
    analysis = result.value.analysis
    assert analysis.issues_of(Severity.ERROR) == []
    found = next(i for i in analysis.issues if i.type is IssueType.SCREEN_DIRECTION)
    assert found.surfaced and found.spans[0].asset_id == asset
    assert float(found.spans[1].boundary.numerator / found.spans[1].boundary.denominator) == 3.0
    assert result.value.asset.metadata["processing_type"] == DOCUMENT_TYPE
    stored = app.container.resolve(ContinuityCatalog).latest(asset)
    assert isinstance(stored, Ok) and stored.value is not None and stored.value[0] == analysis
    assert analysis.risk_between(asset, 2.0, 3.0) is not None


def test_an_identical_rerun_is_reused_and_a_changed_cut_judges_only_that_cut(
    app: Application, tmp_path: Path
) -> None:
    asset, analyzer = reversal(app, tmp_path)
    three = sequence(
        clip("c1", asset, 0.0, 2.0, 0.0),
        clip("c2", asset, 3.0, 4.0, 2.0),
        clip("c3", asset, 0.5, 1.5, 3.0),
    )
    first = run(analyzer, three)
    assert isinstance(first, Ok)
    assert first.value.analysis.metrics.evaluated == 2

    again = run(analyzer, three)
    changed = sequence(
        clip("c1", asset, 0.0, 2.0, 0.0),
        clip("c2", asset, 3.0, 4.0, 2.0),
        clip("c3", asset, 0.6, 1.5, 3.0),
        plan="ep1",
        version=2,
    )
    after = run(analyzer, changed)

    assert isinstance(again, Ok) and not again.value.created
    assert isinstance(after, Ok) and after.value.created
    assert after.value.analysis.analysis_version == 2
    assert after.value.analysis.supersedes == first.value.asset.id
    assert after.value.analysis.metrics.reused == 1 and after.value.analysis.metrics.evaluated == 1

    def first_cut(analysis: ContinuityAnalysis) -> set[str]:
        return {i.issue_id for i in analysis.issues_at("tr:c1>c2")}

    assert first_cut(first.value.analysis) == first_cut(after.value.analysis) != set()
    record = after.value.analysis.record("tr:c1>c2")
    assert record is not None and record.reused
    assert len(app.container.resolve(ContinuityCatalog).history(asset)) == 2


def test_a_persons_decision_follows_the_issue_into_the_next_analysis(
    app: Application, tmp_path: Path
) -> None:
    asset, analyzer = reversal(app, tmp_path)
    first = run(analyzer, two_clips(asset))
    assert isinstance(first, Ok)
    found = first.value.analysis.issues
    assert {i.type for i in found} >= {IssueType.SCREEN_DIRECTION}

    decided = run(
        analyzer,
        two_clips(asset),
        decisions=tuple(
            IssueDecision(
                issue_id=i.issue_id, decision=Decision.REJECTED, note="a deliberate U-turn"
            )
            for i in found
        ),
    )
    later = run(analyzer, two_clips(asset, version=2))

    assert isinstance(decided, Ok) and isinstance(later, Ok)
    assert all(
        (kept := later.value.analysis.issue(i.issue_id)) is not None
        and kept.decision is Decision.REJECTED
        for i in found
    )
    assert later.value.analysis.surfaced() == ()
    assert later.value.analysis.risk_between(asset, 2.0, 3.0) == (
        0.1,
        "continuity analysis: the findings were rejected by a person",
    )


def test_without_a_video_analysis_the_cut_is_unresolved_and_the_result_partial(
    app: Application, tmp_path: Path
) -> None:
    asset = import_clip(app, tmp_path / "clips", "bare")
    analyzer = app.container.resolve(ContinuityAnalyzer)

    result = run(analyzer, two_clips(asset.id))

    assert isinstance(result, Ok), result
    analysis = result.value.analysis
    assert analysis.issues == () and analysis.state.value == "partial"
    assert "picture" in analysis.transitions[0].evidence_missing
    assert next(s for s in analysis.stages if s.stage == "picture").state.value == "not_available"


def test_a_sequence_with_no_cut_or_a_broken_range_is_refused(
    app: Application, tmp_path: Path
) -> None:
    asset, analyzer = reversal(app, tmp_path)

    single = run(analyzer, sequence(clip("c1", asset, 0.0, 2.0, 0.0)))
    contiguous = run(
        analyzer, sequence(clip("c1", asset, 0.0, 2.0, 0.0), clip("c2", asset, 2.0, 4.0, 2.0))
    )
    broken = run(
        analyzer, sequence(clip("c1", asset, 2.0, 1.0, 0.0), clip("c2", asset, 3.0, 4.0, 1.0))
    )

    assert isinstance(single, Err) and isinstance(single.error, NothingToJudge)
    assert isinstance(contiguous, Err) and isinstance(contiguous.error, NothingToJudge)
    assert isinstance(broken, Err) and isinstance(broken.error, InvalidSequence)
