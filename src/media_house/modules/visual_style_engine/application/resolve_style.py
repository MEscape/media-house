"""Use case: resolve the channel style for a project and store it (pinned) in the library."""

from dataclasses import dataclass, field, replace

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.visual_style_engine.application.ports import FontFiles
from media_house.modules.visual_style_engine.application.results import VisualStyleResults
from media_house.modules.visual_style_engine.domain.errors import (
    InvalidStyle,
    InvalidStyleConfig,
    UnknownIdentityVersion,
    VisualStyleError,
)
from media_house.modules.visual_style_engine.domain.identity import (
    CURRENT_IDENTITY_VERSION,
    IDENTITIES,
)
from media_house.modules.visual_style_engine.domain.model import (
    ChannelIdentity,
    ResolvedStyle,
    StyleRef,
)
from media_house.modules.visual_style_engine.domain.profile import StyleConfig
from media_house.modules.visual_style_engine.domain.resolve import resolve_style
from media_house.modules.visual_style_engine.domain.serialization import (
    digest,
    style_from_json,
    style_to_json,
)
from media_house.modules.visual_style_engine.domain.validation import identity_digest
from media_house.modules.visual_style_engine.domain.values import (
    PROFILE_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    STYLE_TAG,
    QcCategory,
    QcIssue,
    Severity,
)
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.filesystem import AppPaths


@dataclass(frozen=True, slots=True)
class BrandAssetBinding:
    """A brand asset slot (``logo``, ``fire_plate``, ...) bound to a Media Library asset."""

    slot: str
    asset_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolveStyleCommand:
    """Only ``anchor_asset_id`` is required (the no-speech path has no project or story format).

    ``width``/``height``: the output; omitted -> the profile's native aspect at 1080 short side.
    ``identity_version``: None = the version this anchor's stored style is pinned to (else the
    current one); a number = exactly that version (an upgrade happens only when asked).
    """

    anchor_asset_id: str
    project_id: str | None = None
    width: int | None = None
    height: int | None = None
    config: StyleConfig = field(default_factory=StyleConfig)
    story_format: str | None = None
    identity_version: int | None = None
    brand_assets: tuple[BrandAssetBinding, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolveStyleResult:
    style: ResolvedStyle
    document: MediaAssetDto
    ref: StyleRef
    created: bool  # False: an identical style was already stored and is returned as is


type ResolveStyleError = MediaError | VisualStyleError


class ResolveStyle:
    """Resolves, checks (identity QC, fonts, glyphs) and stores a ``visual_style`` document."""

    def __init__(
        self, library: MediaLibrary, fonts: FontFiles, paths: AppPaths, clock: Clock
    ) -> None:
        self._library = library
        self._fonts = fonts
        self._documents = DerivedDocuments(library, paths)
        self._results = VisualStyleResults(library)
        self._clock = clock

    def execute(
        self, command: ResolveStyleCommand
    ) -> Result[ResolveStyleResult, ResolveStyleError]:
        anchor = self._library.get(command.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        identity = self._identity(command)
        if isinstance(identity, Err):
            return identity
        bound: dict[str, tuple[str, str]] = {}
        for binding in command.brand_assets:
            if binding.slot in bound:
                return Err(InvalidStyleConfig(f"brand asset slot {binding.slot} bound twice"))
            asset = self._library.get(binding.asset_id)
            if isinstance(asset, Err):
                return asset
            bound[binding.slot] = (asset.value.id, asset.value.checksum)
        try:
            style = resolve_style(
                identity.value,
                command.config,
                created_at=self._clock.now(),
                width=command.width,
                height=command.height,
                story_format=command.story_format,
                project_id=command.project_id,
                anchor_asset_id=anchor.value.id,
                brand_assets=bound,
                extra_checks=self._font_checks(identity.value),
            )
        except VisualStyleError as exc:
            return Err(exc)
        critical = [i.message for i in style.checks if i.severity is Severity.CRITICAL]
        if critical:
            return Err(InvalidStyle(critical))

        key: dict[str, JsonValue] = {
            "project_id": command.project_id,
            "identity": f"{identity.value.id}:{identity.value.version}",
            "identity_digest": identity_digest(identity.value),
            "profile": f"{style.profile}:{style.profile_version}",
            "output": f"{style.output.width}x{style.output.height}",
            "fingerprint": style.fingerprint,
            "overrides": digest(style.overrides),
            "versions": {"schema": SCHEMA_VERSION, "profile_schema": PROFILE_VERSION},
        }
        spec = DocumentSpec(RESULT_OPERATION, SCHEMA_VERSION, key)
        fingerprint = self._documents.fingerprint(anchor.value.id, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        hit = self._documents.load(anchor.value.id, fingerprint.value, style_from_json)
        if hit is not None:
            return Ok(_result(hit[0], hit[1], created=False))

        earlier = [
            a
            for a in self._results.history(anchor.value.id)
            if a.metadata.get("project_id") == command.project_id
            and a.metadata.get("profile") == style.profile
            and a.metadata.get("output") == key["output"]
        ]
        style = replace(
            style,
            style_version=len(earlier) + 1,
            supersedes=earlier[0].id if earlier else None,
        )
        stored = self._documents.store(
            anchor.value,
            style_to_json(style),
            filename="visual_style.json",
            spec=spec,
            display_name=f"{anchor.value.display_name} (visual style {style.profile}"
            f" {key['output']})",
            metadata={
                "project_id": command.project_id,
                "profile": style.profile,
                "output": key["output"],
                "identity_version": style.identity_version,
                "fingerprint": style.fingerprint,
                "style_version": style.style_version,
                "supersedes": style.supersedes,
            },
        )
        if isinstance(stored, Err):
            return stored
        tagged = self._library.add_tags(stored.value.id, (STYLE_TAG,))
        if isinstance(tagged, Err):
            return tagged
        return Ok(_result(style, tagged.value, created=True))

    def _identity(self, command: ResolveStyleCommand) -> Result[ChannelIdentity, ResolveStyleError]:
        version = command.identity_version
        if version is None:
            pinned = self._results.latest(command.anchor_asset_id, project_id=command.project_id)
            if isinstance(pinned, Err):
                return pinned
            version = pinned.value[0].identity_version if pinned.value else CURRENT_IDENTITY_VERSION
        found = IDENTITIES.get(version)
        return Ok(found) if found is not None else Err(UnknownIdentityVersion(version))

    def _font_checks(self, identity: ChannelIdentity) -> list[QcIssue]:
        issues: list[QcIssue] = []
        for face in identity.faces:
            wanted = identity.required_glyphs if face.coverage is None else face.coverage
            missing = self._fonts.missing_glyphs(face, wanted)
            if isinstance(missing, Err):
                message = missing.error.user_message
                code = "font.unavailable"
            elif missing.value:
                message = f"{face.family} {face.weight} lacks glyphs {missing.value!r}"
                code = "font.glyphs_missing"
            else:
                continue
            issues.append(
                QcIssue(
                    code=code,
                    severity=Severity.ERROR,
                    category=QcCategory.TECHNICAL,
                    message=message,
                    item_id=face.key,
                )
            )
        return issues


def _result(style: ResolvedStyle, document: MediaAssetDto, *, created: bool) -> ResolveStyleResult:
    ref = StyleRef(
        document_id=document.id,
        fingerprint=style.fingerprint,
        identity_version=style.identity_version,
        profile=style.profile,
    )
    return ResolveStyleResult(style=style, document=document, ref=ref, created=created)
