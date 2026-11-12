"""A workflow wired like ``ProductionProjectModule`` does, with fakes where the outside world is."""

from dataclasses import dataclass

from media_house.modules.content_intelligence.application.contracts import TextGenerator
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
    EvidenceSource,
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
from media_house.modules.production_project.infrastructure.folder import LocalProjectFolder
from media_house.modules.style_memory.application.guide import GuideStyle
from media_house.modules.style_memory.application.library import SeedStyleMemory
from media_house.modules.style_memory.application.memory import RecordPreference
from tests.support.fakes import FixedClock
from tests.unit.production_project.fakes import InMemoryProjectStore
from tests.unit.style_memory.fakes import InMemoryStyleStore


@dataclass(frozen=True)
class World:
    workflow: ProductionWorkflowService
    projects: ProjectCatalog
    store: InMemoryProjectStore
    style_store: InMemoryStyleStore
    clock: FixedClock


def build_world(
    evidence: EvidenceSource,
    generator: TextGenerator | None = None,
    runner: VariantRunner | None = None,
    timelines: TimelineSource | None = None,
) -> World:
    clock = FixedClock()
    style_store = InMemoryStyleStore()
    SeedStyleMemory(style_store, clock).execute()
    style = GuideStyle(style_store)
    recorder = RecordPreference(style_store, clock)
    store = InMemoryProjectStore()
    projects = ProjectCatalog(store, clock)
    folder = LocalProjectFolder()
    exporter = FileDocumentExporter()
    briefs = BuildBriefDocuments(projects, style, clock, exporter)
    guide = BuildRecordingGuide(projects)
    gate = CheckGate(projects, style)
    evaluate = RecordEvaluation(projects, style, recorder, clock)
    workflow = ProductionWorkflowService(
        projects,
        ReadBrief(DocumentBriefReader()),
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
        IngestFiles(projects, folder, evidence, clock),
        SettleFlag(projects, clock),
        guide,
        BuildContext(projects, style, clock),
        EditContext(projects, clock),
        evaluate,
        ChooseContext(projects, clock),
        BuildMetadata(projects, style, clock),
        gate,
        ExportPackage(projects, gate, briefs, guide, folder, exporter),
        AttachTimeline(projects, timelines) if timelines else None,
        RunVariant(projects, style, runner, timelines, evaluate) if runner and timelines else None,
    )
    return World(workflow, projects, store, style_store, clock)
