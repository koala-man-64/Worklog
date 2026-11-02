"""Validated write contracts. Unknown fields and coercive booleans are rejected."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

Revision = Annotated[StrictInt, Field(ge=0)]
State = Literal["open", "doing", "blocked", "done"]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expected(Command):
    revision: Revision


class ItemCreate(Command):
    repositoryIds: list[str] = Field(default_factory=list, max_length=100)
    title: str
    description: str = ""
    adoUrl: str | None = None
    roadmapId: str | None = None
    stepId: str | None = None
    day: str | None = None
    worked: StrictBool = False


class ItemUpdate(Expected):
    repositoryIds: list[str] = Field(default_factory=list, max_length=100)
    title: str | None = None
    description: str | None = None
    status: State | None = None
    adoUrl: str | None = None
    roadmapId: str | None = None
    stepId: str | None = None


class EntryUpdate(Expected):
    planned: StrictBool | None = None
    worked: StrictBool | None = None
    outcome: str | None = None


class Reflection(Expected):
    text: str


class Reorder(Expected):
    ids: list[str]


class Move(Expected):
    direction: Literal["up", "down"]


class Reschedule(Expected):
    itemId: str
    targetDate: str
    targetRevision: Revision = 0


class NoteCreate(Command):
    ownerType: Literal["item", "roadmap", "step"]
    ownerId: str
    title: str = ""
    text: str = ""


class NoteUpdate(Expected):
    title: str | None = None
    text: str | None = None


class TaskCreate(Command):
    itemId: str
    text: str
    done: StrictBool = False


class TaskUpdate(Expected):
    text: str | None = None
    done: StrictBool | None = None


class ReferenceCreate(Command):
    itemId: str
    path: str


class LinkCreate(Command):
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    url: str
    title: str = ""
    description: str = ""


class LinkUpdate(Expected):
    url: str | None = None
    title: str | None = None
    description: str | None = None


class ChatCreate(Command):
    repositoryIds: list[str] = Field(default_factory=list, max_length=100)
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    chatId: str
    title: str
    createdDate: str | None = None
    provider: str = ""
    model: str = ""
    repos: list[str] = Field(default_factory=list)
    stepIds: list[str] = Field(default_factory=list)


class ChatUpdate(Expected):
    repositoryIds: list[str] = Field(default_factory=list, max_length=100)
    chatId: str | None = None
    title: str | None = None
    createdDate: str | None = None
    provider: str | None = None
    model: str | None = None
    repos: list[str] | None = None
    stepIds: list[str] | None = None


class GithubRefCreate(Command):
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    kind: Literal["commit", "pr"]
    url: str
    reference: str = ""
    title: str = ""
    description: str = ""
    chatId: str | None = None


class GithubRefUpdate(Expected):
    kind: Literal["commit", "pr"] | None = None
    url: str | None = None
    reference: str | None = None
    title: str | None = None
    description: str | None = None
    chatId: str | None = None


class QuestionCreate(Command):
    roadmapId: str
    title: str
    details: str = ""


class QuestionUpdate(Expected):
    title: str | None = None
    details: str | None = None
    answer: str | None = None
    status: Literal["open", "answered"] | None = None


class DecisionCreate(Command):
    roadmapId: str
    title: str
    rationale: str = ""
    decidedDate: str | None = None


class DecisionUpdate(Expected):
    title: str | None = None
    rationale: str | None = None
    decidedDate: str | None = None


class PersonCreate(Command):
    name: str
    role: str = ""
    notes: str = ""


class PersonUpdate(Expected):
    name: str | None = None
    role: str | None = None
    notes: str | None = None


class PersonLinkCreate(Command):
    personId: str
    targetType: Literal["item", "roadmap"]
    targetId: str
    relationship: str = ""


class PersonLinkRemove(Command):
    personId: str
    targetType: Literal["item", "roadmap"]
    targetId: str


class RoadmapContent(Command):
    title: str
    overview: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)


class RoadmapCreate(RoadmapContent):
    parentId: str | None = None


class RoadmapRevision(RoadmapContent):
    revision: Revision
    note: str = ""


class RoadmapMove(Expected):
    parentId: str | None


class Restore(Expected):
    number: Annotated[StrictInt, Field(ge=1)]


class Progress(Expected):
    status: State


class ImportCommit(Command):
    title: str
    overview: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    roadmapId: str | None = None
    revision: Revision | None = None


class Operation(Command):
    action: Literal["repository.create", "repository.update", "repository.archive", "repository.recover",
                    "item.create", "item.update", "item.archive", "item.recover", "entry.upsert",
                    "note.create", "note.update", "task.create", "task.update",
                    "roadmap.create", "roadmap.revise", "roadmap.move", "progress.set",
                    "person.create", "person.update", "person.archive", "person.recover",
                    "person.link", "person.unlink",
                    "link.create", "link.update", "link.archive", "link.recover",
                    "chat.create", "chat.update", "chat.archive", "chat.recover",
                    "github.create", "github.update", "github.archive", "github.recover",
                    "question.create", "question.update", "question.archive", "question.recover",
                    "decision.create", "decision.update", "decision.archive", "decision.recover"]
    payload: dict[str, Any]


class ProposalCreate(Command):
    title: str
    rationale: str
    operations: list[Operation] = Field(default_factory=list)
    supersedesId: str | None = None


class ProposalUpdate(Expected):
    title: str | None = None
    rationale: str | None = None
    operations: list[Operation] | None = None


class CredentialCreate(Command):
    label: str
    # A name from GET /discussions/avatars; omitted draws the least-used one.
    avatar: str | None = Field(default=None, min_length=1, max_length=100)


class CredentialRotate(Command):
    label: str | None = None


class SettingsUpdate(Command):
    timezone: str
