"""Analyse a recording or transcript and print what Content Intelligence found.

    uv run python scratch/demo_content_intelligence.py [path/to/media_or_transcript] [fast|standard|deep]
    uv run python scratch/demo_content_intelligence.py --sample [fast|standard|deep]
    uv run python scratch/demo_content_intelligence.py --sample --no-gui

Runs in a throw-away library under ~/.media_house_scratch. Content Intelligence
understands what a recording communicates: topics, boundaries, key statements,
cross-passage relationships (dependencies, answers, repetition), informational
scores, roles, and editorial opportunities for visuals / b-roll and on-screen graphics.
Every score is advisory evidence for editing; this module never modifies media.
"""

import json
import shutil
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.content_intelligence.application.contracts import (
    AnalyzeContentCommand,
    AudioUse,
    ContentAnalysis,
    ContentAnalyzer,
    ContentConfig,
    KeyItem,
    Opportunity,
    Profile,
    Relationship,
    ScoreName,
    SemanticSegment,
    SourceSegment,
    SourceTranscript,
    SourceWord,
    Span,
    Topic,
    VideoUse,
    analysis_to_json,
    highlights,
    opportunities,
    redundancy_links,
    removal_candidates,
    standalone_candidates,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok

SAMPLE_LINES = (
    "Welcome to today's technical briefing.",
    "Today we examine lithium battery architecture and fast-charging dynamics.",
    "What is a modern lithium battery?",
    "A lithium battery stores electrochemical energy inside tightly sealed chemical cells.",
    "Battery capacity is primarily measured in watt hours or milliampere hours.",
    "For example, a standard smartphone battery contains approximately 15 watt hours.",
    "Charging the battery at excessive speeds generates intense internal cell heat.",
    "Sustained elevated temperature degrades the lithium battery electrolyte over time.",
    "Now let's switch gears to sustainable home gardening.",
    "Tomatoes require abundant direct sunlight and consistent soil moisture.",
    "The garden soil should be generously enriched with organic compost.",
    "Plant young tomato seedlings outdoors shortly after the final seasonal frost.",
    "Organic compost keeps the topsoil moist and supplies vital nutrients to tomato roots.",
    "Unobstructed sunlight remains the single most critical factor for sweet ripe tomatoes.",
    "Finally, let's examine our annual operating budget.",
    "The yearly budget primarily covers staff salaries and office facilities rent.",
    "We recently decreased our quarterly marketing budget by 10 percent.",
    "The executive finance committee reviews the budget every calendar quarter.",
    "Staff salaries represent the largest single expense category in the yearly budget.",
    "Commercial facility rent remains our second largest recurring budget expenditure.",
)


def create_sample_transcript() -> SourceTranscript:
    """Build a realistic multi-topic transcript with word timings for demonstration."""
    clock = 0.0
    segments: list[SourceSegment] = []
    gap = 0.5
    seconds_per_word = 0.35
    for idx, line in enumerate(SAMPLE_LINES):
        clock += gap if idx > 0 else 0.0
        start = clock
        words: list[SourceWord] = []
        for token in line.split():
            words.append(SourceWord(text=token, start=clock, end=clock + seconds_per_word))
            clock += seconds_per_word
        segments.append(
            SourceSegment(
                id=idx,
                text=line,
                start=start,
                end=clock,
                words=tuple(words),
            )
        )
    return SourceTranscript(
        segments=tuple(segments),
        language="en",
        duration=round(clock, 2),
        origin="supplied",
    )


def load_transcript_from_json(path: Path) -> SourceTranscript | None:
    """Parse common JSON transcript formats into a SourceTranscript."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Warning: Failed to parse JSON from {path}: {exc}")
        return None

    if not isinstance(raw, dict):
        return None

    if "segments" in raw and isinstance(raw["segments"], list):
        segments: list[SourceSegment] = []
        for s in raw["segments"]:
            if not isinstance(s, dict):
                continue
            words_list: list[SourceWord] = []
            for w in s.get("words", []):
                if isinstance(w, dict):
                    word_text = str(w.get("text", w.get("raw_word", "")))
                    if word_text:
                        words_list.append(
                            SourceWord(
                                text=word_text,
                                start=float(w.get("start", 0.0)),
                                end=float(w.get("end", 0.0)),
                            )
                        )
            segments.append(
                SourceSegment(
                    id=int(s.get("id", len(segments))),
                    text=str(s.get("text", "")),
                    start=float(s["start"]) if s.get("start") is not None else None,
                    end=float(s["end"]) if s.get("end") is not None else None,
                    speaker=s.get("speaker"),
                    words=tuple(words_list),
                )
            )
        duration_val = raw.get("duration")
        if duration_val is None and "metadata" in raw and isinstance(raw["metadata"], dict):
            duration_val = raw["metadata"].get("duration")

        lang = raw.get("language")
        if lang is None and "metadata" in raw and isinstance(raw["metadata"], dict):
            lang = raw["metadata"].get("language")

        return SourceTranscript(
            segments=tuple(segments),
            language=str(lang) if lang else "en",
            duration=float(duration_val) if duration_val is not None else None,
            origin="supplied",
        )

    if "text" in raw and isinstance(raw["text"], str):
        return SourceTranscript.from_text(raw["text"], language="en")

    return None


def format_span(span: Span) -> str:
    if span.start is not None and span.end is not None:
        return f"{span.start:7.3f}s - {span.end:7.3f}s ({span.start_basis.value})"
    return "untimed"


def format_detailed_analysis(analysis: ContentAnalysis, requested_profile: str) -> str:
    """Format the ContentAnalysis into a comprehensive, human-readable report."""
    out: list[str] = []

    # -------------------------------------------------------------------------
    # Banner
    # -------------------------------------------------------------------------
    out.append("=" * 84)
    out.append("               MEDIA-HOUSE : CONTENT INTELLIGENCE DETAILED REPORT")
    out.append("=" * 84)

    # -------------------------------------------------------------------------
    # 1. Overview & Provenance
    # -------------------------------------------------------------------------
    out.append("\n[1. OVERVIEW & PROVENANCE]")
    out.append(f"  Analysis ID:         {analysis.analysis_id}")
    out.append(f"  Result State:        {analysis.state.value.upper()}")
    out.append(f"  Timestamp:           {analysis.analysis_timestamp.isoformat()}")
    out.append(f"  Requested Profile:   {requested_profile}")
    out.append(f"  Active Profile:      {analysis.provenance.profile}")
    out.append(
        f"  Similarity Engine:   {analysis.provenance.similarity} ({analysis.provenance.similarity_kind})"
    )
    out.append(f"  Execution Device:    {analysis.provenance.device}")
    out.append(f"  Detected Language:   {analysis.source.language or 'unknown'}")
    dur_str = f"{analysis.source.duration:.2f}s" if analysis.source.duration is not None else "N/A"
    out.append(f"  Media Duration:      {dur_str}")
    out.append(
        f"  Source Origin:       {analysis.source.origin} "
        f"(fingerprint: {analysis.source.transcript_fingerprint[:12]}...)"
    )
    out.append(f"  Upstream Segments:   {analysis.source.upstream_segments}")
    out.append(f"  Syntactic Units:     {len(analysis.units)} sentences")
    out.append(f"  Semantic Segments:   {len(analysis.segments)}")
    out.append(f"  Discovered Topics:   {len(analysis.topics)}")
    out.append(f"  Extracted Key Items: {len(analysis.key_items)}")
    out.append(f"  Cross-Passage Links: {len(analysis.relationships)}")

    # -------------------------------------------------------------------------
    # 2. Pipeline Stages & Performance
    # -------------------------------------------------------------------------
    out.append("\n[2. PIPELINE INPUTS & STAGE REPORTS]")
    out.append("  Inputs:")
    for inp in analysis.inputs:
        out.append(
            f"    - {inp.kind:16s} : {inp.how:12s} "
            f"[{inp.detail or 'no detail'}] "
            f"(asset: {inp.asset_id or 'none'})"
        )

    out.append("  Stages:")
    total_stage_sec = 0.0
    for stg in analysis.stages:
        total_stage_sec += stg.seconds
        cache_info = f", cache={stg.cache.value}" if stg.cache.value != "not_used" else ""
        detail_info = f" - {stg.detail}" if stg.detail else ""
        out.append(
            f"    - {stg.stage:16s} : {stg.state.value:12s} "
            f"[{stg.seconds:6.3f}s{cache_info}]{detail_info}"
        )
    out.append(f"  Total Stage Time:    {total_stage_sec:6.3f}s")

    if analysis.provenance.usage:
        out.append("  Resource Telemetry:")
        for k, v in analysis.provenance.usage.items():
            if k.startswith("seconds."):
                out.append(f"    * {k:24s}: {v:6.3f}s")
            else:
                out.append(f"    * {k:24s}: {v}")

    # -------------------------------------------------------------------------
    # 3. Executive Summary & Key Takeaways
    # -------------------------------------------------------------------------
    out.append("\n[3. EXECUTIVE SUMMARY & KEY TAKEAWAYS]")
    if analysis.summary:
        out.append(f'  Overall Summary: "{analysis.summary.text}"')
        out.append(
            f"    (Origin: {analysis.summary.origin.value}, "
            f"Method: {analysis.summary.method.value}, "
            f"Units: {len(analysis.summary.unit_ids)})"
        )
    else:
        out.append("  Overall Summary: None generated.")

    if analysis.key_takeaways:
        out.append("  Key Takeaways:")
        for idx, tk in enumerate(analysis.key_takeaways, 1):
            out.append(f'    {idx}. "{tk.text}"')
            out.append(f"       [Grounded in units: {', '.join(tk.unit_ids[:4])}]")
    else:
        out.append("  Key Takeaways: None extracted.")

    if analysis.global_observations:
        out.append("  Global Observations:")
        for obs in analysis.global_observations:
            out.append(f"    * [{obs.kind}] {obs.text}")

    # -------------------------------------------------------------------------
    # 4. Topic Hierarchy
    # -------------------------------------------------------------------------
    out.append("\n[4. TOPIC HIERARCHY]")
    for topic in analysis.topics:
        span_str = format_span(topic.span)
        parent_str = f" (child of {topic.parent_id})" if topic.parent_id else ""
        out.append(
            f"  * [{topic.topic_id}] {topic.label} "
            f"[{topic.level.value.upper()}]{parent_str}"
        )
        out.append(f"      Span:       {span_str}")
        out.append(f"      Terms:      {', '.join(topic.label_terms)}")
        out.append(f"      Segments:   {', '.join(topic.segment_ids)}")
        if topic.summary:
            out.append(f'      Summary:    "{topic.summary.text}"')
        if topic.description:
            out.append(f'      Detail:     "{topic.description}"')

    # -------------------------------------------------------------------------
    # 5. Semantic Segments Breakdown
    # -------------------------------------------------------------------------
    out.append("\n[5. DETAILED SEMANTIC SEGMENTS]")
    for seg in analysis.segments:
        span_str = format_span(seg.span)
        topics_str = ", ".join(seg.topic_ids) if seg.topic_ids else "none"
        out.append(f"\n  === Segment {seg.segment_id} ({span_str}) | Topics: [{topics_str}] ===")

        if seg.boundary:
            weak_str = " (WEAK BOUNDARY)" if seg.boundary.weak else ""
            out.append(
                f"    Boundary:      strength={seg.boundary.strength:.2f} "
                f"before unit {seg.boundary.before_unit}{weak_str}"
            )
        if seg.summary:
            out.append(f'    Summary:       "{seg.summary.text}"')

        # Roles and Rhetoric
        if seg.roles:
            role_strs = [
                f"{r.role.value}" + (f" (conf={r.confidence:.2f})" if r.confidence else "")
                for r in seg.roles
            ]
            out.append(f"    Roles:         {', '.join(role_strs)}")
        if seg.rhetoric:
            out.append(f"    Rhetoric:      {', '.join(m.kind.value for m in seg.rhetoric)}")

        # Scores breakdown
        out.append("    Scores (Advisory Evidence):")
        for sc in seg.scores:
            val_str = f"{sc.value:.2f}" if sc.value is not None else "N/A"
            comp_list = [f"{k}={v:.2f}" for k, v in sc.components.items()]
            comp_str = f" [{', '.join(comp_list)}]" if comp_list else ""
            reason_str = f" ({sc.reason})" if sc.reason else ""
            out.append(f"      * {sc.name.value:20s}: {val_str}{comp_str}{reason_str}")

            if sc.name is ScoreName.REMOVAL_CANDIDATE and sc.counter_evidence:
                counters = [ce.detail or ce.kind for ce in sc.counter_evidence]
                out.append(f"        Counter-evidence against cut: {', '.join(counters)}")

            if sc.requires_context:
                out.append(
                    f"        Context required from units: {', '.join(sc.requires_context)}"
                )

        # Key Items in segment
        seg_keys = [k for k in analysis.key_items if k.item_id in seg.key_item_ids]
        if seg_keys:
            out.append("    Key Items:")
            for ki in seg_keys:
                ki_span = (
                    f"[{ki.span.start:.2f}s]" if ki.span.start is not None else "[untimed]"
                )
                out.append(f'      - [{ki.kind.value:9s}] {ki_span} "{ki.text}"')

        # Opportunities in segment
        if seg.broll or seg.graphics:
            out.append("    Editorial Opportunities:")
            for br in seg.broll:
                cov_str = " (already covered)" if br.already_covered else ""
                out.append(
                    f'      - [B-ROLL]   Concept: "{br.concept}" '
                    f"(category={br.category}, strength={br.strength:.2f}){cov_str}"
                )
                if br.rationale:
                    out.append(f"                   Rationale: {br.rationale}")
            for gr in seg.graphics:
                out.append(
                    f'      - [GRAPHIC]  Concept: "{gr.concept}" '
                    f"(kind={gr.category}, strength={gr.strength:.2f})"
                )
                if gr.rationale:
                    out.append(f"                   Rationale: {gr.rationale}")

        # Links
        if seg.dependency_ids:
            out.append(f"    Dependencies:  {', '.join(seg.dependency_ids)}")
        if seg.redundancy_ids:
            out.append(f"    Redundancies:  {', '.join(seg.redundancy_ids)}")

    # -------------------------------------------------------------------------
    # 6. Cross-Passage Relationships
    # -------------------------------------------------------------------------
    out.append("\n[6. CROSS-PASSAGE RELATIONSHIPS]")
    if analysis.relationships:
        for rel in analysis.relationships:
            adds_str = f" [adds: {rel.adds.value}]" if rel.adds else ""
            conf_str = f" (conf={rel.confidence:.2f})" if rel.confidence else ""
            note_str = f" - {rel.note}" if rel.note else ""
            out.append(
                f"  * {rel.rel_id:8s}: {rel.source.id} --[{rel.kind.value.upper()}]--> "
                f"{rel.target.id}{adds_str}{conf_str}{note_str}"
            )
    else:
        out.append("  No cross-passage relationships detected.")

    # -------------------------------------------------------------------------
    # 7. Editorial Queries & Highlights (Using Domain Query Contracts)
    # -------------------------------------------------------------------------
    out.append("\n[7. CURATED EDITORIAL VIEWS]")

    # Highlights (Short clips / Social)
    hl_clips = highlights(analysis, minimum=0.4, limit=5)
    out.append(f"  Top Highlight Candidates (min score 0.40, found {len(hl_clips)}):")
    if hl_clips:
        for idx, clip in enumerate(hl_clips, 1):
            sc_val = clip.value(ScoreName.HIGHLIGHT)
            sc_str = f"{sc_val:.2f}" if sc_val is not None else "N/A"
            summary_txt = clip.summary.text if clip.summary else "N/A"
            out.append(
                f"    {idx}. [{clip.segment_id}] score={sc_str} "
                f"span={format_span(clip.span)}: \"{summary_txt}\""
            )
    else:
        out.append("    (No segments met highlight threshold)")

    # Standalone Candidates
    sa_clips = standalone_candidates(analysis, minimum=0.6)
    out.append(f"\n  Standalone Candidates (min score 0.60, found {len(sa_clips)}):")
    if sa_clips:
        for idx, clip in enumerate(sa_clips[:5], 1):
            sc_val = clip.value(ScoreName.STANDALONE)
            sc_str = f"{sc_val:.2f}" if sc_val is not None else "N/A"
            summary_txt = clip.summary.text if clip.summary else "N/A"
            out.append(
                f"    {idx}. [{clip.segment_id}] score={sc_str} "
                f"span={format_span(clip.span)}: \"{summary_txt}\""
            )
    else:
        out.append("    (No segments met standalone threshold)")

    # Removal Candidates (Advisory only)
    rm_clips = removal_candidates(analysis, minimum=0.3)
    out.append(f"\n  Removal Candidates (ADVISORY ONLY, found {len(rm_clips)}):")
    if rm_clips:
        for idx, clip in enumerate(rm_clips[:5], 1):
            sc_val = clip.value(ScoreName.REMOVAL_CANDIDATE)
            sc_str = f"{sc_val:.2f}" if sc_val is not None else "N/A"
            sc_obj = clip.score(ScoreName.REMOVAL_CANDIDATE)
            counters = (
                [ce.detail or ce.kind for ce in sc_obj.counter_evidence]
                if sc_obj and sc_obj.counter_evidence
                else ["none"]
            )
            out.append(
                f"    {idx}. [{clip.segment_id}] candidate_score={sc_str} "
                f"span={format_span(clip.span)} | Keep Arguments: {', '.join(counters)}"
            )
    else:
        out.append("    (None identified - all passages provide substantive value)")

    # Redundancy Links
    red_links = redundancy_links(analysis)
    out.append(f"\n  Redundancy & Repetition Links (found {len(red_links)}):")
    if red_links:
        for rlink in red_links:
            out.append(
                f"    * {rlink.source.id} {rlink.kind.value.upper()} {rlink.target.id} "
                f"({rlink.note or 'similar content'})"
            )
    else:
        out.append("    (No significant semantic repetition detected)")

    # Opportunities Summary
    all_opps = opportunities(analysis)
    out.append(f"\n  Top Visual & Graphic Opportunities (found {len(all_opps)}):")
    if all_opps:
        for idx, opp in enumerate(all_opps[:6], 1):
            out.append(
                f'    {idx}. [{opp.kind.value.upper():7s}] strength={opp.strength:.2f} '
                f'concept="{opp.concept}" (segment {opp.segment_id})'
            )
    else:
        out.append("    (No editorial visual opportunities identified)")

    # -------------------------------------------------------------------------
    # 8. Warnings & Limitations
    # -------------------------------------------------------------------------
    out.append("\n[8. WARNINGS & LIMITATIONS]")
    if analysis.warnings:
        out.append("  Warnings:")
        for w in analysis.warnings:
            out.append(f"    ! {w}")
    else:
        out.append("  Warnings: None.")

    out.append("  Limitations:")
    for lim in analysis.limitations:
        out.append(f"    * {lim}")

    # -------------------------------------------------------------------------
    # 9. Full JSON Serialization
    # -------------------------------------------------------------------------
    out.append("\n" + "=" * 84)
    out.append("                            FULL ANALYSIS JSON")
    out.append("=" * 84)
    out.append(analysis_to_json(analysis))

    return "\n".join(out)


class AnalysisViewer(QDialog):
    """Monospaced viewer dialog for Content Intelligence analysis."""

    def __init__(self, text: str, parent: QDialog | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Content Intelligence Analysis Result")
        self.resize(1000, 700)

        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(text)
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 10pt;"
        )
        layout.addWidget(self.text_edit)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        layout.addWidget(self.btn_close)


class Progress:
    """Print job progress updates to terminal."""

    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        total_str = f"/{total}" if total is not None else ""
        print(f"  [{current}{total_str}] {message}", flush=True)


def main() -> int:
    profile_str = "standard"
    path: Path | None = None
    use_sample = False
    no_gui = False

    args = sys.argv[1:]
    if "--no-gui" in args or "--headless" in args:
        no_gui = True
        args = [a for a in args if a not in ("--no-gui", "--headless")]

    if "--sample" in args or "--demo" in args:
        use_sample = True
        args = [a for a in args if a not in ("--sample", "--demo")]

    for candidate in ("fast", "standard", "deep"):
        if candidate in args:
            profile_str = candidate
            args.remove(candidate)
            break

    if args:
        path = Path(args[0])

    # If no file or sample flag was given on the CLI, open a file chooser
    if not use_sample and path is None:
        app_qt = QApplication.instance() or QApplication(sys.argv)
        file_path, _ = QFileDialog.getOpenFileName(
            None,
            "Select a media or transcript file to analyze (or Cancel for sample demo)",
            "",
            "All Supported (*.mp4 *.mov *.mkv *.webm *.avi *.wav *.mp3 *.flac *.m4a *.txt *.json);;"
            "Media Files (*.mp4 *.mov *.mkv *.webm *.avi *.wav *.mp3 *.flac *.m4a);;"
            "Transcript Files (*.txt *.json);;"
            "All Files (*.*)",
        )
        if not file_path:
            print("No file selected. Running demo with built-in multi-topic sample transcript...")
            use_sample = True
        else:
            path = Path(file_path)

    profile = Profile(profile_str) if profile_str in Profile._value2member_map_ else Profile.STANDARD
    config = ContentConfig(profile=profile)

    scratch = Path.home() / ".media_house_scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    app = Application.start(
        StartupOptions(env={}, home=scratch, environment=Environment.DEVELOPMENT)
    )

    try:
        library = app.container.resolve(MediaLibrary)
        analyzer = app.container.resolve(ContentAnalyzer)

        cmd: AnalyzeContentCommand
        if use_sample:
            print(f"Running Content Intelligence on sample transcript (profile: {profile.value})...")
            transcript = create_sample_transcript()
            cmd = AnalyzeContentCommand(transcript=transcript, config=config)
        elif path is not None and path.suffix.lower() == ".txt":
            print(f"Loading text transcript from {path}...")
            text = path.read_text(encoding="utf-8")
            transcript = SourceTranscript.from_text(text, language="en")
            cmd = AnalyzeContentCommand(transcript=transcript, config=config)
        elif path is not None and path.suffix.lower() == ".json":
            print(f"Loading JSON transcript from {path}...")
            transcript = load_transcript_from_json(path)
            if transcript is None:
                print(f"Could not parse valid transcript from JSON: {path}")
                return 1
            cmd = AnalyzeContentCommand(transcript=transcript, config=config)
        elif path is not None:
            print(f"Importing media file into scratch library: {path}...")
            imported = library.import_file(path)
            if not isinstance(imported, Ok):
                print(f"Could not import media: {imported.error}")
                return 1
            cmd = AnalyzeContentCommand(
                source_asset_id=imported.value.asset.id,
                config=config,
                audio=AudioUse.TRANSCRIPT,
                video=VideoUse.OFF,
            )
        else:
            print("No input provided.")
            return 1

        print("Executing Content Intelligence analysis...")
        ctx = JobContext.detached()
        ctx = JobContext(ctx.job_id, ctx.cancellation, Progress())
        result = analyzer.execute(cmd, ctx)

        if not isinstance(result, Ok):
            print(f"\nAnalysis failed: {result.error}")
            return 1

        analysis = result.value.analysis
        output_text = format_detailed_analysis(analysis, profile.value)

        # Print to terminal
        print(f"\n{output_text}")

        # Display in GUI viewer dialog unless --no-gui was requested
        if not no_gui:
            if "app_qt" not in locals():
                app_qt = QApplication.instance() or QApplication(sys.argv)
            viewer = AnalysisViewer(output_text)
            viewer.exec()

        return 0
    finally:
        app.shutdown()


if __name__ == "__main__":
    sys.exit(main())
