"""A work item's scratchpad: its one active note, holding what were separate notes as sections."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from .common import NOTE_LIMIT

TITLE = "Scratchpad"
# Titles that name no section: every new scratchpad's, the old "Add note" default, and none.
UNTITLED = frozenset({"", TITLE, "New note"})


def size(text: str) -> int:
    """Bytes, as the note text limit counts them."""
    return len(text.encode("utf-8"))


def section(title: str, text: str) -> str:
    """A note as a section of a scratchpad, its title as the heading when the title says something.

    The scratchpad shows no title, so a title kept only in its column would be hidden.
    """
    body = text.strip("\r\n")
    heading = title.strip()
    if heading in UNTITLED:
        return body
    return f"## {heading}\n\n{body}" if body.strip() else f"## {heading}"


def joined(sections: Iterable[str]) -> str:
    return "\n\n".join(part for part in sections if part.strip())


def fold(notes: list[tuple[str, str]]) -> tuple[str, bool]:
    """Join a work item's (title, text) notes, oldest first, into the text of its scratchpad.

    Returns the text and whether the first note's title is in it (or had nothing to say).
    The first note is the scratchpad's own and is always kept whole: under its heading when
    that fits the limit every later edit enforces, otherwise as it was, its title left in its
    column. Later notes follow, in order, while the text fits; the rest stay whole only in
    their archived notes, and a closing line says how many.
    """
    sections = [section(title, text) for title, text in notes]
    titled = size(sections[0]) <= NOTE_LIMIT
    if not titled:
        sections[0] = notes[0][1]
    whole = joined(sections)
    if size(whole) <= NOTE_LIMIT:
        return whole, titled
    room = len(_left_out(len(sections))) + 2
    kept = [sections[0]]
    for part in sections[1:]:
        if size(joined([*kept, part])) + room > NOTE_LIMIT:
            break
        kept.append(part)
    closing = joined([*kept, _left_out(len(sections) - len(kept))])
    return (closing if size(closing) <= NOTE_LIMIT else joined(kept)), titled


def _left_out(count: int) -> str:
    return f"*{count} more {'note' if count == 1 else 'notes'} did not fit here and stay whole under Archived notes.*"


def find(conn: sqlite3.Connection, item_id: str) -> Any:
    """The work item's active note, (id, title, text, revision); the v8 index allows at most one."""
    return conn.execute("SELECT id,title,text,revision FROM notes WHERE owner_type='item' AND owner_id=? AND archived=0",
                        (item_id,)).fetchone()
