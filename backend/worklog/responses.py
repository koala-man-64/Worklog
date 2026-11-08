"""What reads return, typed (consolidation step 5.3, A-03).

Each model is the shape a route already answered with, which backend/tests/golden pins: typing a
route changes no answer, only what docs/openapi.json, and the client types generated from it, say.
The plan's *Rules* hold here: booleans read from 0/1 columns, timestamps as stored text, with or
without microseconds, and null only where the column allows it. A model forbids keys it doesn't
name, so a route that starts answering with another key fails its golden test rather than dropping
the key unseen.
"""

from __future__ import annotations

from typing import Any, Literal

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


class Event(Answer):
    """One entry of a record's history in full: the record before and after the change, as written then."""

    id: str
    entityId: str
    kind: str
    actorId: str
    actorLabel: str
    actorKind: str
    createdAt: str
    localDate: str
    # As the change wrote them, and as compaction may have thinned them: any JSON.
    before: Any
    after: Any


class Attachment(Answer):
    """An attached file's details; its bytes are read on their own."""

    id: str
    ownerType: Literal["item", "roadmap", "step", "proposal", "repository"]
    ownerId: str
    name: str
    mime: str
    size: int
    sha256: str
    archived: bool
    revision: int
    createdAt: str


class RecordPerson(Answer):
    """A person tied to a work item, roadmap or repository, as that record lists them."""

    id: str
    personId: str
    name: str
    personTitle: str
    personTeam: str
    relationship: str
    archived: bool
    createdAt: str


class DailyEntry(Answer):
    id: str
    itemId: str
    date: str
    planned: bool
    worked: bool
    outcome: str
    position: int
    revision: int


class LinkedRoadmap(Answer):
    id: str
    title: str
    archived: bool


class LinkedStep(Answer):
    id: str
    title: str
    roadmapId: str
    roadmapTitle: str
    retired: bool


class ListedGithubReference(GithubReference):
    """A GitHub reference as its owner lists it, with the title of the chat that made it."""

    chatTitle: str | None


class WorkItem(Answer):
    """A work item with everything its page shows."""

    id: str
    title: str
    description: str
    status: Literal["open", "doing", "waiting", "blocked", "done"]
    adoUrl: str | None
    roadmapId: str | None
    stepId: str | None
    backlogOrder: int
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    # The person's stack rank, 1 first; null when the item is done or archived.
    rank: int | None
    rankedCount: int
    repositoryIds: list[str]
    notes: list[Note]
    attachments: list[Attachment]
    links: list[WebLink]
    chats: list[TrackedChat]
    github: list[ListedGithubReference]
    tasks: list[Task]
    references: list[FileReference]
    people: list[RecordPerson]
    dailyEntries: list[DailyEntry]
    history: list[Event]
    linkedRoadmap: LinkedRoadmap | None
    linkedStep: LinkedStep | None


class RegisteredBy(Answer):
    # repositories.registered_by_kind has no CHECK. A repository with none recorded has no registrant at all.
    kind: str
    label: str
    agentId: str | None


class Repository(Answer):
    """A repository of the catalog, as other records embed it."""

    id: str
    name: str
    url: str
    remoteIdentity: str
    duplicateOf: str | None
    mergedInto: str | None
    description: str
    archived: bool
    # Null when no registrant is recorded: coordination.repository_record gives none for an empty kind.
    registeredBy: RegisteredBy | None
    reviewed: bool
    revision: int
    createdAt: str
    updatedAt: str


class RepositoryDetail(Repository):
    """A repository as its own read returns it: with its discussion (v22) and the people tied to it."""

    # Null for a repository archived before v22 and not recovered since.
    discussionId: str | None
    people: list[RecordPerson]


class WorkItemRecord(Answer):
    """A work item's own fields, as other records embed it."""

    id: str
    title: str
    description: str
    status: Literal["open", "doing", "waiting", "blocked", "done"]
    adoUrl: str | None
    roadmapId: str | None
    stepId: str | None
    backlogOrder: int
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    repositoryIds: list[str]


class RoadmapRecord(Answer):
    """A roadmap's own fields, as other records embed it."""

    id: str
    title: str
    overview: str
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str


class PersonWorkItem(Answer):
    linkId: str
    personId: str
    itemId: str
    relationship: str
    createdAt: str
    item: WorkItemRecord


class PersonRoadmap(Answer):
    linkId: str
    personId: str
    roadmapId: str
    relationship: str
    createdAt: str
    roadmap: RoadmapRecord


class PersonRepository(Answer):
    linkId: str
    personId: str
    repositoryId: str
    relationship: str
    createdAt: str
    repository: Repository


class Person(Answer):
    """A person with what they are tied to and their history."""

    id: str
    name: str
    title: str
    team: str
    description: str
    email: str
    phone: str
    chat: str
    location: str
    notes: str
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    roadmaps: list[PersonRoadmap]
    workItems: list[PersonWorkItem]
    repositories: list[PersonRepository]
    history: list[Event]
