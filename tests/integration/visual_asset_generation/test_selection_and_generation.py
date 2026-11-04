"""Reuse before generation, on a real library; generation is gated and honestly unavailable."""

import zlib
from pathlib import Path

import pytest
from PIL import Image

from media_house.modules.media_library.application.contracts import SourceType
from media_house.modules.visual_asset_generation.application.contracts import (
    GENERATED_TAG,
    GeneratedImage,
    GenerateImageCommand,
    GenerationKind,
    GenerationNotAvailable,
    GenerationRefused,
    GenerationRequest,
    ImageGenerationService,
    ImageSelector,
    SelectImagesCommand,
    SourceTier,
    StageState,
)
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, Ok
from tests.integration.visual_asset_generation.conftest import Lab

pytestmark = pytest.mark.integration


def add(lab: Lab, name: str, *, tags: tuple[str, ...] = (), group: str | None = None) -> str:
    path = lab.tmp / f"{name}.png"
    code = zlib.crc32(name.encode())  # distinct content: the library stores identical files once
    Image.new("RGB", (64, 64), (code % 256, code // 256 % 256, code // 65536 % 256)).save(path)
    imported = lab.library.import_file(
        path, display_name=name, tags=tags, group_ids=(group,) if group else ()
    )
    assert isinstance(imported, Ok)
    return imported.value.asset.id


def request(kind: GenerationKind = GenerationKind.ILLUSTRATION) -> GenerationRequest:
    return GenerationRequest(
        request_id="g1",
        concept="Sicherheitsabstand Illustration",
        kind=kind,
        prompt="flat illustration of a safety distance marker",
        reason="b-roll gap gap-7 has no footage",
        seed=7,
    )


class FakeGenerator:
    """Stands in for a future generator so the registration path can be proven."""

    def identity(self) -> str:
        return "fake-generator"

    def unavailable(self) -> str | None:
        return None

    def generate(
        self, request: GenerationRequest, destination: Path, cancellation: CancellationToken
    ) -> GeneratedImage:
        Image.new("RGB", (request.width, request.height), (10, 20, 30)).save(destination)
        return GeneratedImage(model="fake", model_version="0", seed=request.seed)


def test_existing_images_are_ranked_uploads_first_and_similarity_is_not_available(
    lab: Lab,
) -> None:
    project = lab.library.create_group("Project")
    assert isinstance(project, Ok)
    upload = add(lab, "Titan Raketenbatterie")
    approved = add(lab, "titan rakete approved", group=project.value.id)
    brand = add(lab, "Titan Logo Rakete", tags=("brand",))
    add(lab, "Wolf Maskottchen")

    selection = lab.selector().execute(
        SelectImagesCommand(concept="Titan Rakete", project_group_ids=(project.value.id,))
    )

    assert [c.asset_id for c in selection.candidates] == [upload, approved, brand]
    assert [c.tier for c in selection.candidates] == [
        SourceTier.UPLOAD,
        SourceTier.APPROVED,
        SourceTier.BRAND,
    ]
    states = {s.stage: s.state for s in selection.stages}
    assert states["picture_text_similarity"] is StageState.NOT_AVAILABLE


def test_the_wired_selector_reports_no_gaps_when_no_broll_analysis_is_stored(lab: Lab) -> None:
    source = add(lab, "clip")
    gaps = lab.resolve(ImageSelector).for_gaps(source)
    assert isinstance(gaps, Ok) and gaps.value == ()


@pytest.mark.parametrize("kind", [GenerationKind.LOGO, GenerationKind.PRODUCT_PHOTO])
def test_logos_and_product_photos_are_refused(lab: Lab, kind: GenerationKind) -> None:
    result = lab.resolve(ImageGenerationService).execute(
        GenerateImageCommand(request=request(kind)), JobContext.detached()
    )
    assert isinstance(result, Err) and isinstance(result.error, GenerationRefused)


def test_an_existing_match_refuses_generation(lab: Lab) -> None:
    add(lab, "Sicherheitsabstand Illustration")
    result = lab.generation(FakeGenerator()).execute(
        GenerateImageCommand(request=request()), JobContext.detached()
    )
    assert isinstance(result, Err) and isinstance(result.error, GenerationRefused)


def test_an_allowed_request_is_not_available_on_this_installation(lab: Lab) -> None:
    result = lab.resolve(ImageGenerationService).execute(
        GenerateImageCommand(request=request()), JobContext.detached()
    )
    assert isinstance(result, Err) and isinstance(result.error, GenerationNotAvailable)
    assert lab.library.search().total == 0  # nothing was faked


def test_a_generated_picture_says_so_and_keeps_its_provenance(lab: Lab) -> None:
    result = lab.generation(FakeGenerator()).execute(
        GenerateImageCommand(request=request()), JobContext.detached()
    )
    assert isinstance(result, Ok), result
    generated = result.value.asset
    assert generated.source_type is SourceType.AI_GENERATED
    assert GENERATED_TAG in generated.tags
    assert generated.metadata["generated"] is True
    assert generated.metadata["generation_seed"] == 7
    assert generated.metadata["generation_request"]["kind"] == "illustration"  # type: ignore[index,call-overload]
    again = lab.selector().execute(SelectImagesCommand(concept="Sicherheitsabstand Illustration"))
    assert [c.tier for c in again.candidates] == [SourceTier.GENERATED]  # reused next time
