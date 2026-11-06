"""Validated write contracts. Unknown fields and coercive booleans are rejected."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

Revision = Annotated[StrictInt, Field(ge=0)]
# A ticket's or a roadmap step's state.
State = Literal["open", "doing", "blocked", "done"]
# A work item's state, which can also be waiting on someone else (v15).
ItemState = Literal["open", "doing", "waiting", "blocked", "done"]


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
    status: ItemState | None = None
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


class Rank(Expected):
    """revision is the item's own; a rank past the last moves the item last."""
    rank: Annotated[StrictInt, Field(ge=1)]


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


class TaskMove(Expected):
    """revision is the task's own; position is its new place among the work item's active tasks,
    1 first, and a place past the last moves it last."""
    position: Annotated[StrictInt, Field(ge=1)]


class ReferenceCreate(Command):
    itemId: str
    path: str


class AttachmentCreate(Command):
    """A copy of a file uploaded to a proposal as evidence, attached when Worklog applies the proposal."""
    ownerType: Literal["item", "roadmap", "step", "repository"]
    ownerId: str
    attachmentId: str
    # The proposal that holds the file. Worklog sets it when it applies the proposal; an agent cannot.
    proposalId: str


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


class ChatMessageCreate(Command):
    text: str = Field(min_length=1, max_length=1_000_000)


class InboxReceive(Command):
    chatId: str | None = Field(default=None, min_length=1, max_length=500)


class ChatConfirmation(Command):
    # The identity, communication_agents.id, as the chat page's recipients and claims give it.
    agentId: str = Field(min_length=1, max_length=200)


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


TicketKind = Literal["bug", "incident", "task"]
TicketPriority = Literal["low", "normal", "high", "urgent"]


class TicketCreate(Command):
    repositoryIds: list[str] = Field(default_factory=list, max_length=100)
    title: str
    description: str = ""
    kind: TicketKind = "bug"
    priority: TicketPriority = "normal"
    status: State = "open"
    resolution: str = ""
    labels: list[str] = Field(default_factory=list, max_length=20)
    itemId: str | None = None
    # The person ("local-user") or an agent identity; advisory only.
    assigneeId: str | None = None


class TicketUpdate(Expected):
    repositoryIds: list[str] | None = Field(default=None, max_length=100)
    title: str | None = None
    description: str | None = None
    kind: TicketKind | None = None
    priority: TicketPriority | None = None
    status: State | None = None
    resolution: str | None = None
    labels: list[str] | None = Field(default=None, max_length=20)
    # Null unlinks the work item or clears the assignee; omitted keeps them.
    itemId: str | None = None
    assigneeId: str | None = None


class TicketCommentCreate(Command):
    text: str = Field(min_length=1, max_length=100_000)


class ResourceCreate(Command):
    title: str
    # What the page is for, in a sentence: lists show it in place of the page's text.
    summary: str = ""
    body: str = ""


class ResourceUpdate(Expected):
    title: str | None = None
    summary: str | None = None
    body: str | None = None


class PersonCreate(Command):
    name: str
    title: str = ""
    team: str = ""
    # Who they are and what to go to them for, in a sentence or two; notes hold the rest.
    description: str = ""
    email: str = ""
    phone: str = ""
    # A chat handle, such as a Teams or Slack name.
    chat: str = ""
    # Where they are, or their time zone.
    location: str = ""
    notes: str = ""


class PersonUpdate(Expected):
    name: str | None = None
    title: str | None = None
    team: str | None = None
    description: str | None = None
    email: str | None = None
    phone: str | None = None
    chat: str | None = None
    location: str | None = None
    notes: str | None = None


# What a person can be tied to; relationship is their role there, such as Owner or Reviewer.
PersonTarget = Literal["item", "roadmap", "repository"]


class PersonLinkCreate(Command):
    personId: str
    targetType: PersonTarget
    targetId: str
    relationship: str = ""


class PersonLinkRemove(Command):
    personId: str
    targetType: PersonTarget
    targetId: str


# What an agent can be tied to (v17), in agent_links.SUMMARIES' order; relationship is its role there.
AgentTieTarget = Literal["item", "roadmap", "step", "repository", "person", "resource", "discussion", "chat",
                         "proposal", "ticket", "agent", "question", "decision"]


class AgentLinkSet(Command):
    """The person names the agent. An agent ties only itself, so it may leave agentId out."""
    agentId: str | None = None
    targetType: AgentTieTarget
    targetId: str
    relationship: str = ""


class AgentLinkRemove(Command):
    agentId: str | None = None
    targetType: AgentTieTarget
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
                    "link.create", "link.update", "link.archive", "link.recover", "attachment.create",
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
    timezone: str | None = None
    # False applies an agent's proposal as soon as it is submitted.
    requireProposalApproval: StrictBool | None = None
    # False keeps agents from registering repositories; they propose repository.create instead.
    agentRepositoryRegistration: StrictBool | None = None
