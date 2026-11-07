"""``list_derived`` returns every derivative, not only the first page (the oldest 200)."""

from typing import Any

from media_house.modules.media_library.application.queries import MediaQueries
from media_house.modules.media_library.domain.query import MediaQuery, Page


class _Repository:
    """A repository that holds ``total`` assets (their ordinal numbers) and pages them."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.queries: list[MediaQuery] = []

    def search(self, query: MediaQuery) -> Page[int]:
        self.queries.append(query)
        everything = list(range(self.total))
        items = everything[query.offset : query.offset + query.page_size]
        return Page(items, self.total, query.page, query.page_size)


class _Mapper:
    def many(self, assets: list[Any]) -> list[Any]:
        return list(assets)


def _queries(total: int) -> tuple[MediaQueries, _Repository]:
    repository = _Repository(total)
    return MediaQueries(repository, _Mapper(), object()), repository  # type: ignore[arg-type]


def test_a_recording_with_more_than_one_page_of_derivatives_lists_all_of_them() -> None:
    queries, repository = _queries(MediaQuery.MAX_PAGE_SIZE * 2 + 37)

    found: list[Any] = queries.list_derived("anchor")

    assert found == list(range(MediaQuery.MAX_PAGE_SIZE * 2 + 37))  # the newest ones included
    assert [q.page for q in repository.queries] == [1, 2, 3]


def test_a_small_recording_is_read_in_one_query() -> None:
    queries, repository = _queries(5)

    found: list[Any] = queries.list_derived("anchor")
    assert found == [0, 1, 2, 3, 4]
    assert len(repository.queries) == 1
