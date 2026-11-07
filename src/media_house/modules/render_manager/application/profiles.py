"""Render profiles for other modules: list them, resolve one against a timeline, and compute the
``output_config_hash`` an export authorization is bound to."""

from dataclasses import dataclass

from media_house.modules.media_library.application.contracts import MediaError
from media_house.modules.render_manager.application.views import timeline_view
from media_house.modules.render_manager.domain.errors import RenderManagerError
from media_house.modules.render_manager.domain.profile import (
    PROFILES,
    RenderProfile,
    ResolvedSettings,
    get_profile,
    output_config_hash,
    profile_problems,
    resolve_settings,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.errors import Err, NotFoundError, Ok, Result

type ProfileFailure = RenderManagerError | MediaError | NotFoundError


@dataclass(frozen=True, slots=True)
class ProfileResolution:
    profile: RenderProfile
    settings: ResolvedSettings
    config_hash: str
    #: Why the profile does not fit this timeline (empty when it fits).
    problems: tuple[str, ...]


class RenderProfileBook:
    """Implements ``RenderProfiles``."""

    def __init__(self, catalog: BuiltTimelineCatalog) -> None:
        self._catalog = catalog

    def profiles(self) -> tuple[RenderProfile, ...]:
        return PROFILES

    def profile(self, profile_id: str) -> Result[RenderProfile, RenderManagerError]:
        try:
            return Ok(get_profile(profile_id))
        except RenderManagerError as exc:
            return Err(exc)

    def resolve(
        self,
        profile_id: str,
        *,
        anchor_asset_id: str,
        timeline_id: str | None = None,
        custom: RenderProfile | None = None,
    ) -> Result[ProfileResolution, ProfileFailure]:
        """The profile with the (latest) timeline's size and rate filled in, and its hash."""
        try:
            profile = custom or get_profile(profile_id)
        except RenderManagerError as exc:
            return Err(exc)
        latest = self._catalog.latest(anchor_asset_id, timeline_id=timeline_id)
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(
                NotFoundError(
                    "No built timeline", user_message="There is no built timeline to render."
                )
            )
        view = timeline_view(latest.value[0])
        settings = resolve_settings(
            profile, width=view.width, height=view.height, frame_rate=view.frame_rate
        )
        return Ok(
            ProfileResolution(
                profile,
                settings,
                output_config_hash(settings),
                profile_problems(profile, width=view.width, height=view.height),
            )
        )

    def config_hash(
        self, profile_id: str, *, anchor_asset_id: str, timeline_id: str | None = None
    ) -> Result[str, ProfileFailure]:
        """The hash of what this profile would output for the timeline: what an export
        authorization for this output is given for."""
        resolved = self.resolve(
            profile_id, anchor_asset_id=anchor_asset_id, timeline_id=timeline_id
        )
        if isinstance(resolved, Err):
            return resolved
        return Ok(resolved.value.config_hash)
