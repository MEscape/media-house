"""Thumbnail page: view model and passive view against a fake facade."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from media_house.modules.control_center.application.thumbnail_studio import (
    AssetRow,
    StudioCatalogue,
    ThumbnailForm,
    ThumbnailStudio,
    VariantRow,
)
from media_house.modules.control_center.presentation.viewmodels.thumbnail_viewmodel import (
    ThumbnailViewModel,
)
from media_house.modules.control_center.presentation.views.thumbnail_view import ThumbnailView
from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.errors.handler import ErrorReport
from tests.support.fakes import ImmediateJobScheduler

pytestmark = pytest.mark.presentation


@dataclass
class FakeStudio:
    picture: str
    calls: list[tuple[str, object]] = field(default_factory=list)
    reactions: list[AssetRow] = field(default_factory=list)
    design_error: str = ""

    def catalogue(self) -> StudioCatalogue:
        return StudioCatalogue(
            videos=(AssetRow("vid", "lidl video"),),
            pictures=(AssetRow("pk1", "Seven Kings", self.picture), AssetRow("logo", "Lidl")),
            reactions=tuple(self.reactions),
        )

    def add_reaction(self, path: Path) -> Result[AssetRow, str]:
        self.calls.append(("add_reaction", path.name))
        row = AssetRow("me2", "new reaction", self.picture)
        self.reactions.append(row)
        return Ok(row)

    def import_picture(self, path: Path) -> Result[AssetRow, str]:
        self.calls.append(("import", path.name))
        return Ok(AssetRow("pk2", path.name, self.picture))

    def suggest(
        self, video_ids: tuple[str, ...], ctx: JobContext
    ) -> Result[tuple[AssetRow, ...], str]:
        self.calls.append(("suggest", video_ids))
        return Ok((AssetRow("frame1", "frame", self.picture, "face 40% of frame"),))

    def design(self, form: ThumbnailForm, ctx: JobContext) -> Result[tuple[VariantRow, ...], str]:
        self.calls.append(("design", form))
        if self.design_error:
            return Err(self.design_error)
        return Ok(
            (
                VariantRow("a", "A · clean", "t_a", self.picture),
                VariantRow("b", "B · 04:30", "t_b", self.picture, ("The price is cut off.",)),
            )
        )

    def export(self, asset_id: str, destination: Path) -> Result[Path, str]:
        self.calls.append(("export", (asset_id, destination.name)))
        return Ok(destination)


@pytest.fixture
def studio(tmp_path: Path) -> FakeStudio:
    picture = tmp_path / "picture.png"
    Image.new("RGB", (1920, 1080), (36, 95, 167)).save(picture)
    return FakeStudio(str(picture), reactions=[AssetRow("me", "reaction GOPR1984", str(picture))])


@pytest.fixture
def page(
    qtbot: QtBot, studio: FakeStudio, dispatcher: UiDispatcher, unhandled_errors: list[ErrorReport]
) -> ThumbnailView:
    jobs = UiJobRunner(
        ImmediateJobScheduler(), dispatcher, on_unhandled_error=unhandled_errors.append
    )
    vm = ThumbnailViewModel(cast(ThumbnailStudio, studio), jobs, StatusReporter())
    view = ThumbnailView(vm)
    qtbot.addWidget(view)
    vm.load()
    return view


def test_the_reaction_collection_comes_first(page: ThumbnailView) -> None:
    assert page.reaction.currentData() == "me"
    assert [page.reaction.itemData(i) for i in range(page.reaction.count())] == [
        "me",
        "pk1",
        "logo",
    ]
    assert page.video.itemData(1) == "vid"


def test_design_sends_the_brief_and_shows_the_variants(
    page: ThumbnailView, studio: FakeStudio, monkeypatch: pytest.MonkeyPatch
) -> None:
    page.video.setCurrentIndex(1)
    page.line1.setText("Erstmals")
    page.line2.setText("online!")
    page.products.item(0).setCheckState(Qt.CheckState.Checked)
    page.logo.setCurrentIndex(page.logo.findData("logo"))
    page.badge.setText("04:30")
    page.design_button.click()

    form = studio.calls[-1][1]
    assert isinstance(form, ThumbnailForm)
    assert (form.video_id, form.line1, form.line2, form.reaction_id) == (
        "vid",
        "Erstmals",
        "online!",
        "me",
    )
    assert (form.product_ids, form.logo_id, form.badge) == (("pk1",), "logo", "04:30")
    assert page.variant_list.count() == 2
    assert page.preview.has_picture() and page.feed.has_picture()
    assert page.export_button.isEnabled()

    page.variant_list.setCurrentRow(1)
    assert "cut off" in page.issues.text()
    monkeypatch.setattr(page, "save_file", lambda suggested: str(Path("C:/out") / suggested))
    page.export_button.click()
    assert studio.calls[-1] == ("export", ("t_b", "thumbnail_b.jpg"))


def test_a_refused_brief_shows_the_reason(page: ThumbnailView, studio: FakeStudio) -> None:
    studio.design_error = "Write the headline: one or two short lines."
    page.design_button.click()
    assert page.error.isVisibleTo(page)
    assert page.error.text() == studio.design_error
    assert not page.export_button.isEnabled()


def test_an_uploaded_reaction_joins_the_collection_and_is_selected(
    page: ThumbnailView, studio: FakeStudio, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(page, "pick_file", lambda: "C:/pics/me_shocked.jpg")
    page.upload_reaction.click()
    assert ("add_reaction", "me_shocked.jpg") in studio.calls
    assert page.reaction.currentData() == "me2"


def test_footage_moments_can_be_picked_as_reaction(page: ThumbnailView, studio: FakeStudio) -> None:
    page.video.setCurrentIndex(1)
    page.from_footage.click()
    assert ("suggest", ("vid",)) in studio.calls
    assert page.candidates.count() == 1
    assert page.candidates.item(0).toolTip() == "face 40% of frame"
