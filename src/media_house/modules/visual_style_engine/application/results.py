"""Read stored visual styles (pinned per project) without resolving anything new."""

from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaQuery,
)
from media_house.modules.visual_style_engine.domain.errors import (
    InvalidStyleDocument,
    StyleRefMismatch,
    UnknownIdentityVersion,
)
from media_house.modules.visual_style_engine.domain.identity import IDENTITIES
from media_house.modules.visual_style_engine.domain.model import (
    AffectedStyle,
    ResolvedStyle,
    StyleRef,
)
from media_house.modules.visual_style_engine.domain.resolve import compare_identity
from media_house.modules.visual_style_engine.domain.serialization import style_from_json
from media_house.modules.visual_style_engine.domain.values import RESULT_OPERATION, STYLE_TAG
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type _LoadError = MediaError | InvalidStyleDocument


class VisualStyleResults:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def latest(
        self,
        anchor_asset_id: str,
        *,
        project_id: str | None = None,
        profile: str | None = None,
        output: str | None = None,
    ) -> Result[tuple[ResolvedStyle, MediaAssetDto] | None, MediaError]:
        """The PINNED style: the newest stored one of the anchor (optionally of one project,
        profile and output ``"1920x1080"``). ``Ok(None)``: none stored yet."""
        stored = [
            a
            for a in self.history(anchor_asset_id)
            if (project_id is None or a.metadata.get("project_id") == project_id)
            and (profile is None or a.metadata.get("profile") == profile)
            and (output is None or a.metadata.get("output") == output)
        ]
        for asset in stored:
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidStyleDocument):
                return Err(loaded.error)
            _log.warning(
                "Stored visual style unusable, trying an older one",
                asset_id=asset.id,
                reason=str(loaded.error),
            )
        return Ok(None)

    def history(self, anchor_asset_id: str) -> list[MediaAssetDto]:
        """Every stored style of the anchor, newest first (earlier ones are never changed)."""
        stored = [
            a
            for a in self._library.list_derived(anchor_asset_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
        ]
        return sorted(stored, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(self, document_id: str) -> Result[ResolvedStyle, _LoadError]:
        path = self._library.local_path(document_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(style_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidStyleDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            return Err(InvalidStyleDocument(str(exc)))

    def load_ref(self, ref: StyleRef) -> Result[ResolvedStyle, _LoadError | StyleRefMismatch]:
        """The style a plan references; ``Err(StyleRefMismatch)`` if it is not the same look."""
        loaded = self.load(ref.document_id)
        if isinstance(loaded, Ok) and loaded.value.fingerprint != ref.fingerprint:
            return Err(StyleRefMismatch(ref.document_id))
        return loaded

    def affected_by(
        self, identity_version: int
    ) -> Result[list[AffectedStyle], UnknownIdentityVersion]:
        """Every stored style of ANOTHER identity version, compared with the same request
        resolved under ``identity_version``; ``affected`` = its look would change."""
        identity = IDENTITIES.get(identity_version)
        if identity is None:
            return Err(UnknownIdentityVersion(identity_version))
        found: list[AffectedStyle] = []
        page = 1
        while True:
            listing = self._library.search(
                MediaQuery(tags=frozenset({STYLE_TAG}), page=page, page_size=200)
            )
            for asset in listing.items:
                loaded = self.load(asset.id)
                if isinstance(loaded, Err):
                    _log.warning("Stored visual style unreadable", asset_id=asset.id)
                    continue
                if loaded.value.identity_version != identity_version:
                    found.append(compare_identity(loaded.value, identity, asset.id))
            if not listing.has_next:
                return Ok(found)
            page += 1
