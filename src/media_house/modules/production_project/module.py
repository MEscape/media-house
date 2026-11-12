"""Module registration: the production project owns ``production_project.db``.

Media, probe data, transcripts and video analysis stay with their owners: the evidence source
reads them through their public contracts and never recomputes them. The style comes from the
style memory, a language model (when one is configured and allowed) from Content Intelligence.
Without a model every step still has its deterministic path.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.content_intelligence.application.contracts import TextGenerator
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.production_project.application.brief_step import (
    BuildBriefDocuments,
    DecideRisk,
    ExtractBrief,
    ReadBrief,
    RunRiskPanel,
)
from media_house.modules.production_project.application.context_step import (
    BuildContext,
    ChooseContext,
    EditContext,
    RecordEvaluation,
    RunVariant,
)
from media_house.modules.production_project.application.contracts import ProductionWorkflow
from media_house.modules.production_project.application.final_step import (
    AttachTimeline,
    BuildMetadata,
    CheckGate,
    ExportPackage,
)
from media_house.modules.production_project.application.material_step import (
    BuildRecordingGuide,
    IngestFiles,
    PlanMaterial,
    SettleFlag,
)
from media_house.modules.production_project.application.ports import (
    BriefReader,
    DocumentExporter,
    EvidenceSource,
    ProjectFolder,
    ProjectStore,
    TimelineSource,
    VariantRunner,
)
from media_house.modules.production_project.application.projects import ProjectCatalog
from media_house.modules.production_project.application.story_step import (
    Brainstorm,
    ChooseConcept,
    EditFacts,
    EditScript,
    GenerateScript,
    LintScript,
)
from media_house.modules.production_project.application.workflow import ProductionWorkflowService
from media_house.modules.production_project.infrastructure.brief_reader import DocumentBriefReader
from media_house.modules.production_project.infrastructure.documents import FileDocumentExporter
from media_house.modules.production_project.infrastructure.evidence import LibraryEvidenceSource
from media_house.modules.production_project.infrastructure.folder import LocalProjectFolder
from media_house.modules.production_project.infrastructure.sqlite_store import SqliteProjectStore
from media_house.modules.production_project.infrastructure.timeline_source import (
    BuiltTimelineSource,
)
from media_house.modules.style_memory.application.contracts import (
    PreferenceRecorder,
    StyleGuidance,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.shared.filesystem import AppPaths


class ProductionProjectModule:
    name: str = "production_project"

    def register(self, container: Container) -> None:
        container.register_factory(
            ProjectStore,
            lambda c: SqliteProjectStore(c.resolve(AppPaths).data_dir / "production_project.db"),
        )
        container.register_factory(BriefReader, lambda _c: DocumentBriefReader())
        container.register_factory(DocumentExporter, lambda _c: FileDocumentExporter())
        container.register_factory(ProjectFolder, lambda _c: LocalProjectFolder())
        container.register_factory(
            EvidenceSource,
            lambda c: LibraryEvidenceSource(
                c.resolve(MediaLibrary),
                c.resolve(InspectionCatalog),
                c.resolve(AudioCatalog),
                c.resolve(VideoCatalog),
            ),
        )
        container.register_factory(
            TimelineSource, lambda c: BuiltTimelineSource(c.resolve(BuiltTimelineCatalog))
        )
        container.register_factory(
            ProjectCatalog, lambda c: ProjectCatalog(c.resolve(ProjectStore), c.resolve(Clock))
        )
        container.register_factory(ProductionWorkflow, _workflow)


def _workflow(c: Container) -> ProductionWorkflowService:
    projects = c.resolve(ProjectCatalog)
    style = c.resolve(StyleGuidance)
    recorder = c.resolve(PreferenceRecorder)
    clock = c.resolve(Clock)
    generator = c.resolve(TextGenerator)
    folder = c.resolve(ProjectFolder)
    exporter = c.resolve(DocumentExporter)
    briefs = BuildBriefDocuments(projects, style, clock, exporter)
    guide = BuildRecordingGuide(projects)
    gate = CheckGate(projects, style)
    evaluate = RecordEvaluation(projects, style, recorder, clock)
    timelines = c.resolve(TimelineSource)
    return ProductionWorkflowService(
        projects,
        ReadBrief(c.resolve(BriefReader)),
        ExtractBrief(projects, style, clock, generator),
        RunRiskPanel(projects, style, clock),
        DecideRisk(projects, recorder, clock),
        briefs,
        Brainstorm(projects, style, clock, generator),
        ChooseConcept(projects, recorder, clock),
        GenerateScript(projects, style, clock, generator),
        EditScript(projects, style, recorder, clock, generator),
        EditFacts(projects, clock),
        LintScript(projects, style),
        PlanMaterial(projects, clock),
        IngestFiles(projects, folder, c.resolve(EvidenceSource), clock),
        SettleFlag(projects, clock),
        guide,
        BuildContext(projects, style, clock),
        EditContext(projects, clock),
        evaluate,
        ChooseContext(projects, clock),
        BuildMetadata(projects, style, clock),
        gate,
        ExportPackage(projects, gate, briefs, guide, folder, exporter),
        AttachTimeline(projects, timelines),
        RunVariant(projects, style, c.resolve(VariantRunner), timelines, evaluate),
    )
