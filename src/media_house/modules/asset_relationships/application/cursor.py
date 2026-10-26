"""The page cursor: the last relationship id, plus what the lineage scan counted.

The cursor is opaque to callers (``RelationshipPage.next_cursor`` goes back in as
``RelationshipQuery.cursor``). It carries the library's total number of derivatives as the first
scan saw it, so the next page can notice that the number changed in between. That detects SOME
concurrent changes only: an insertion plus a deletion leaves the total as it was, so an unchanged
total does not prove the pages fit together.
"""


def encode(last_id: str, scan_total: int | None) -> str:
    return last_id if scan_total is None else f"{last_id}:{scan_total}"


def decode(cursor: str | None) -> tuple[str | None, int | None]:
    """``(last id, total the earlier page saw)``; the total is ``None`` if the cursor has none."""
    if cursor is None:
        return None, None
    last_id, separator, total = cursor.partition(":")
    if separator and total.isdecimal():
        return last_id, int(total)
    return cursor, None
