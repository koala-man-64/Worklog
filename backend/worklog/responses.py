"""What reads return, typed (consolidation step 5.3, A-03).

Each model is the shape a route already answered with, which backend/tests/golden pins: typing a
route changes no answer, only what docs/openapi.json, and the client types generated from it, say.
The plan's *Rules* hold here: booleans read from 0/1 columns, timestamps as stored text, with or
without microseconds, and null only where the column allows it. A model forbids keys it doesn't
name, so a route that starts answering with another key fails its golden test rather than dropping
the key unseen.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Note(Answer):
    id: str
    ownerType: Literal["item", "roadmap", "step"]
    ownerId: str
    title: str
    text: str
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str


class Task(Answer):
    id: str
    itemId: str
    text: str
    done: bool
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str


class FileReference(Answer):
    id: str
    itemId: str
    path: str
    archived: bool
    revision: int
    createdAt: str


class WebLink(Answer):
    id: str
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    url: str
    title: str
    description: str
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str


class TrackedChat(Answer):
    id: str
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    chatId: str
    title: str
    createdDate: str
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    provider: str
    model: str
    # Free-text repository labels from before the catalog, read only (K8).
    repos: list[str]
    stepIds: list[str]
    repositoryIds: list[str]


class GithubReference(Answer):
    id: str
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    kind: Literal["commit", "pr"]
    url: str
    reference: str
    title: str
    description: str
    chatId: str | None
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str


class Question(Answer):
    id: str
    roadmapId: str
    title: str
    details: str
    answer: str
    status: Literal["open", "answered"]
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str


class Decision(Answer):
    id: str
    roadmapId: str
    title: str
    rationale: str
    decidedDate: str
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str


class Change(Answer):
    """One entry of a record's history as the record's own read lists it: who changed it, how and when."""

    id: str
    kind: str
    actorId: str
    actorLabel: str
    # events.actor_kind has no CHECK, so any text it holds reads back.
    actorKind: str
    createdAt: str


class Resource(Answer):
    id: str
    title: str
    summary: str
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    body: str
    history: list[Change]


class ReleaseCheck(Answer):
    id: str
    releaseId: str
    kind: Literal["notify", "test", "prepare"]
    text: str
    details: str
    personId: str | None
    dueDate: str | None
    status: Literal["open", "doing", "waiting", "blocked", "done"]
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    personName: str | None
    personArchived: bool
    pastDue: bool


class ReminderTarget(Answer):
    type: Literal["item", "ticket", "roadmap", "person", "repository", "chat"]
    id: str
    title: str
    archived: bool


class ReminderAuthor(Answer):
    id: str
    label: str
    kind: Literal["agent", "human"]
    avatar: str | None


class Reminder(Answer):
    """A reminder with its notes and history, as its own read returns it."""

    id: str
    title: str
    date: str
    time: str | None
    state: Literal["open", "done"]
    due: bool
    overdue: bool
    target: ReminderTarget | None
    author: ReminderAuthor
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    doneAt: str | None
    notes: str
    history: list[Change]
