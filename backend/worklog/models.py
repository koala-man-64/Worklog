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
    title: str
    description: str = ""
    adoUrl: str | None = None
    stepId: str | None = None
    day: str | None = None
    worked: StrictBool = False


class ItemUpdate(Expected):
    title: str | None = None
    description: str | None = None
    status: State | None = None
    adoUrl: str | None = None
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


class RoadmapCreate(Command):
    title: str
    overview: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)


class RoadmapRevision(RoadmapCreate):
    revision: Revision
    note: str = ""


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
    action: Literal["item.create", "item.update", "item.archive", "item.recover", "entry.upsert",
                    "note.create", "note.update", "task.create", "task.update",
                    "roadmap.create", "roadmap.revise", "progress.set"]
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


class SettingsUpdate(Command):
    timezone: str
