"""Retrieve the style a generation step should follow (rules, exemplars) and say what was used."""

from media_house.modules.style_memory.application.ports import StyleStore
from media_house.modules.style_memory.application.queries import GuidanceQuery
from media_house.modules.style_memory.domain.guidance import Guidance
from media_house.modules.style_memory.domain.model import Exemplar
from media_house.modules.style_memory.domain.rulebook import select_rules
from media_house.modules.style_memory.domain.values import Origin
from media_house.shared.errors import NotFoundError


class GuideStyle:
    """Implements ``StyleGuidance``."""

    def __init__(self, store: StyleStore) -> None:
        self._store = store

    def guidance(self, query: GuidanceQuery) -> Guidance:
        profile = self._store.load_profile()
        if profile is None:
            raise NotFoundError(
                "No style profile stored",
                user_message="Es gibt noch kein Stilprofil. Lege es zuerst an.",
            )
        rules = [
            r
            for r in self._store.list_rules()
            if query.use_learned or r.origin is not Origin.LEARNED
        ]
        chosen = select_rules(
            rules,
            task=query.task,
            format=query.format,
            client=query.client,
            project_id=query.project_id,
            limit=query.rule_limit,
        )
        exemplars = self._exemplars(query) if query.use_learned else ()
        return Guidance(profile, chosen, exemplars, query.format, query.use_learned)

    def _exemplars(self, query: GuidanceQuery) -> tuple[Exemplar, ...]:
        wanted = {query.task.value, *query.tags}
        pool = [e for e in self._store.list_exemplars() if e.format in {query.format, ""}]
        pool.sort(key=lambda e: (-(e.kind in wanted) - len(wanted & set(e.tags)), e.exemplar_id))
        return tuple(pool[: query.exemplar_limit])
