"""Identities of renders: what makes two requests the same render."""

from dataclasses import dataclass

from media_house.core.domain import json_codec
from media_house.modules.render_manager.domain.values import MODULE_VERSION, RenderType


@dataclass(frozen=True, slots=True)
class FrameRange:
    """A stretch of the timeline in frames (``end_frame`` exclusive)."""

    start_frame: int
    end_frame: int

    def __post_init__(self) -> None:
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("a frame range starts at 0 or later and has at least one frame")

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


def job_key(
    *,
    timeline_id: str,
    content_hash: str,
    render_type: RenderType,
    profile_id: str,
    profile_version: int,
    config_hash: str,
    frames: FrameRange | None,
    backend: str,
) -> str:
    """Two requests with the same key produce the same file, so the second is the first.

    The timeline's CONTENT hash is in it: any change to the video is a new render, and a rebuild
    that changed nothing is not. This is the render manager's dependency-aware invalidation.
    """
    return json_codec.digest(
        {
            "timeline_id": timeline_id,
            "content_hash": content_hash,
            "type": render_type.value,
            "profile": f"{profile_id}@{profile_version}",
            "config": config_hash,
            "range": None if frames is None else [frames.start_frame, frames.end_frame],
            "backend": backend,
            "module": MODULE_VERSION,
        }
    )


def render_id_of(key: str) -> str:
    """The stable id of a render: the same request always has the same id."""
    return f"r-{key[:20]}"


def output_name(
    pattern: str, *, timeline: str, version: int, render_type: str, profile: str, n: int
) -> str:
    """A unique, versioned file name (no path, no extension)."""
    name = pattern.format(
        timeline=timeline, version=version, type=render_type, profile=profile, n=n
    )
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:120]
