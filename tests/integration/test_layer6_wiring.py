"""Every public contract of Layer 6 is wired in the real composition root, and the modules reach
each other only through those contracts (a missing registration would fail here, not in a run)."""

from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.export_validation.application.contracts import (
    ExportValidationCatalog,
    ExportValidator,
)
from media_house.modules.final_polish.application.contracts import FinalPolisher, PolishCatalog
from media_house.modules.quality_control.application.contracts import (
    QualityAnalyzer,
    QualityCatalog,
)
from media_house.modules.render_manager.application.contracts import (
    RenderCatalog,
    RenderManager,
    RenderProfiles,
    RenderQueueing,
)
from media_house.modules.resolve_export.application.contracts import (
    ExportApprovalService,
    ExportCatalog,
    FinalExporter,
)
from media_house.modules.review_orchestrator.application.contracts import (
    ReviewCatalog,
    ReviewSessions,
)
from media_house.modules.revision_engine.application.contracts import (
    RevisionCatalog,
    RevisionDecider,
    RevisionExecutorService,
    RevisionPlanner,
    RollbackPlanner,
)
from media_house.shared.configuration import Environment

pytestmark = pytest.mark.integration

CONTRACTS = (
    ReviewSessions,
    ReviewCatalog,
    QualityAnalyzer,
    QualityCatalog,
    RevisionPlanner,
    RevisionDecider,
    RevisionExecutorService,
    RollbackPlanner,
    RevisionCatalog,
    FinalPolisher,
    PolishCatalog,
    RenderManager,
    RenderQueueing,
    RenderProfiles,
    RenderCatalog,
    ExportValidator,
    ExportValidationCatalog,
    ExportApprovalService,
    FinalExporter,
    ExportCatalog,
)


@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda c: c.__name__)
def test_every_layer6_contract_resolves_in_the_real_composition_root(
    contract: type, tmp_path: Path
) -> None:
    app = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    try:
        assert app.container.resolve(contract) is not None
    finally:
        app.shutdown()
