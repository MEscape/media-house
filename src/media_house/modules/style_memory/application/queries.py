"""Commands and queries of the style memory."""

from dataclasses import dataclass

from media_house.modules.style_memory.domain.values import Task


@dataclass(frozen=True, slots=True)
class GuidanceQuery:
    task: Task
    format: str
    client: str = ""
    project_id: str = ""
    #: False = "ohne Lernregeln generieren": no learned rule and no exemplar, for comparison.
    use_learned: bool = True
    rule_limit: int = 24
    exemplar_limit: int = 2
    tags: tuple[str, ...] = ()
