"""What other modules may react to, published once when a render attempt ended."""

from dataclasses import dataclass

from media_house.shared.events import ApplicationEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class RenderFinished(ApplicationEvent):
    render_id: str
    report_id: str
    render_type: str
    timeline_version: int
    #: ``completed`` / ``failed`` / ``cancelled``
    status: str
    #: The file passed its checks (a completed job whose file failed them is ``False``).
    succeeded: bool
