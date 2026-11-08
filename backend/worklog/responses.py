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


class ResourceListed(Answer):
    """A resource as lists show it, without the page's text."""

    id: str
    title: str
    summary: str
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str


class Resource(ResourceListed):
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


class Author(Answer):
    """Who wrote a reminder, a ticket or a ticket's comment, as written then, with the avatar their
    agent identity has now."""

    id: str
    label: str
    kind: Literal["agent", "human"]
    # Null for the person, and for an agent identity with none or that is gone.
    avatar: str | None


class ReminderListed(Answer):
    """A reminder as lists show it, without its notes or history."""

    id: str
    title: str
    date: str
    time: str | None
    state: Literal["open", "done"]
    due: bool
    overdue: bool
    target: ReminderTarget | None
    author: Author
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    doneAt: str | None


class Reminder(ReminderListed):
    """A reminder with its notes and history, as its own read returns it."""

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


class DiscussedRepository(Repository):
    """A repository with its own discussion's id (v22), as the list, its read and a resolved address show it."""

    # Null for a merged repository, whose discussion is tagged with the survivor now, and for one
    # archived before v22 and not recovered since (coordination.with_discussion).
    discussionId: str | None


class RepositoryDetail(DiscussedRepository):
    """A repository as its own read returns it: with its discussion and the people tied to it."""

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


class PlanStep(Answer):
    """A step's own fields, as a roadmap's steps and its saved revisions give them. A parent's
    status is derived from its leaves."""

    id: str
    title: str
    description: str
    parentId: str | None
    position: int
    # steps.status has no CHECK.
    status: str
    revision: int
    retired: bool


class RoadmapStep(PlanStep):
    """A step as its roadmap lists it, with its notes and files."""

    notes: list[Note]
    attachments: list[Attachment]


class RoadmapVersion(Answer):
    number: int
    createdAt: str
    note: str


class RoadmapImport(Answer):
    """A plan import committed to the roadmap; its original is read on its own."""

    id: str
    name: str
    sha256: str
    createdAt: str


class FamilyRoadmap(Answer):
    """A roadmap above or below another, as that one lists it."""

    id: str
    title: str
    archived: bool


class RoadmapWorkItem(WorkItemRecord):
    """A work item on the roadmap or one of its steps, with the people tied to it."""

    people: list[RecordPerson]


class Roadmap(Answer):
    """A roadmap with everything its page shows."""

    id: str
    title: str
    overview: str
    parentId: str | None
    revision: int
    archived: bool
    createdAt: str
    updatedAt: str
    steps: list[RoadmapStep]
    versions: list[RoadmapVersion]
    notes: list[Note]
    attachments: list[Attachment]
    links: list[WebLink]
    chats: list[TrackedChat]
    github: list[ListedGithubReference]
    questions: list[Question]
    decisions: list[Decision]
    history: list[Event]
    ancestors: list[FamilyRoadmap]
    children: list[FamilyRoadmap]
    workItems: list[RoadmapWorkItem]
    people: list[RecordPerson]
    imports: list[RoadmapImport]


class Step(RoadmapStep):
    """A step as its own read returns it: with its roadmap, and retired or not."""

    roadmapId: str


class History(Answer):
    items: list[Event]
    total: int


class TabCounts(Answer):
    """What a work item's tabs on Today hold, so they say so before the item is read."""

    tasks: int
    tasksDone: int
    links: int
    files: int
    scratchpad: bool


class DayItem(WorkItemRecord):
    rank: int | None


class DayEntry(DailyEntry):
    item: DayItem
    counts: TabCounts
    # The status the item ended the day with, from its history; its status now when it has none by then.
    statusOnDay: str


class WorkedOn(Answer):
    id: str
    title: str
    outcome: str


class Named(Answer):
    id: str
    title: str


class DaySummary(Answer):
    worked: list[WorkedOn]
    completed: list[Named]
    blocked: list[Named]


class Day(Answer):
    date: str
    revision: int
    reflection: str
    reflectionRevision: int
    entries: list[DayEntry]
    summary: DaySummary
    nextItemId: str | None


class PersonListed(Answer):
    """A person as the people list shows them, with how many active records each kind ties them to."""

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
    workItemCount: int
    roadmapCount: int
    repositoryCount: int


class People(Answer):
    items: list[PersonListed]
    total: int


class Resources(Answer):
    items: list[ResourceListed]
    total: int
    limit: int
    offset: int


class Reminders(Answer):
    items: list[ReminderListed]
    total: int
    limit: int
    offset: int


class WorkItemListed(WorkItemRecord):
    rank: int | None
    isBacklog: bool
    people: list[RecordPerson]


class WorkItems(Answer):
    items: list[WorkItemListed]
    total: int
    rankedCount: int
    # The backlog's revision, which a reorder checks.
    collectionRevision: int


class RoadmapListed(Answer):
    id: str
    title: str
    overview: str
    parentId: str | None
    revision: int
    archived: bool
    createdAt: str
    updatedAt: str
    people: list[RecordPerson]


class Roadmaps(Answer):
    items: list[RoadmapListed]
    total: int


class Repositories(Answer):
    items: list[DiscussedRepository]
    total: int
    limit: int
    offset: int


class RepositoryResolution(Answer):
    """What registering an address would do: link to the record holding its remote, or add one."""

    url: str
    remoteIdentity: str
    # Null when no record holds the address's remote yet.
    repository: DiscussedRepository | None


class Health(Answer):
    """Whether this Worklog answers, and which instance and data it serves."""

    app: str
    version: str
    instanceId: str
    # Null only for a database without one in its settings.
    dataId: str | None
    ready: bool


class RecordHistory(Answer):
    """A record's latest history entries the reader may see."""

    items: list[Event]


class Attachments(Answer):
    items: list[Attachment]
    total: int


class TieAgent(Answer):
    """An agent as its ties show it."""

    id: str
    label: str
    avatar: str | None
    # Whether it holds a credential that isn't revoked.
    active: bool


# A tied record as record_kinds.summary gives it: one shape per kind, told apart by their keys, since
# each model forbids the keys it doesn't name.
class RecordSummary(Answer):
    """A roadmap, person, resource or discussion."""

    id: str
    title: str
    archived: bool


class StatusSummary(RecordSummary):
    """A work item or proposal. A proposal is never archived; its status says where it got to."""

    status: str


class StepSummary(StatusSummary):
    """A step or question, with its roadmap. A retired step reads as archived."""

    roadmapId: str
    roadmapTitle: str


class DecisionSummary(RecordSummary):
    roadmapId: str
    roadmapTitle: str


class RepositorySummary(RecordSummary):
    """A repository, by its name. A merged one reads as archived."""

    mergedInto: str | None


class TicketSummary(StatusSummary):
    number: int


class AgentSummary(RecordSummary):
    """An agent identity, by its label. One without a live credential reads as archived."""

    avatar: str | None


TiedRecord = RecordSummary | StatusSummary | StepSummary | DecisionSummary | RepositorySummary | TicketSummary | AgentSummary


class AgentTie(Answer):
    """An agent's tie to a record, with the agent and the record as they stand now."""

    id: str
    agentId: str
    # Null if the agent identity is gone.
    agent: TieAgent | None
    targetType: Literal["item", "roadmap", "step", "repository", "person", "resource", "discussion", "proposal",
                        "ticket", "agent", "question", "decision"]
    targetId: str
    # Null if the record is gone. A draft proposal the reader may not see leaves its tie out.
    target: TiedRecord | None
    relationship: str
    setBy: Literal["human", "agent"]
    createdAt: str


class AgentTies(Answer):
    # The first 500 (agent_links.READ_LIMIT); total counts them all.
    items: list[AgentTie]
    total: int


class CredentialSponsor(Answer):
    """The agent that registered a credential, by its label then."""

    id: str
    label: str


class SettingsCredential(Answer):
    id: str
    label: str
    createdAt: str
    revoked: bool
    # Null for a credential no agent identity is bound to.
    agentId: str | None
    # Null for one issued in Settings.
    registeredBy: CredentialSponsor | None
    # The chat its identity works in; null when its registration named none.
    chatId: str | None
    # The active registrations its identity holds.
    registeredActive: int


class SettingsAgent(Answer):
    id: str
    label: str
    avatar: str | None


class BackupFile(Answer):
    """A backup or raw recovery file, by its file's metadata; Settings reads no hashes."""

    name: str
    size: int
    createdAt: str


class Storage(Answer):
    databaseBytes: int
    backupBytes: int
    # Null unless the last automatic backup failed or the database is past 2 GB.
    warning: str | None


class Settings(Answer):
    timezone: str
    today: str
    version: str
    requireProposalApproval: bool
    agentRepositoryRegistration: bool
    credentials: list[SettingsCredential]
    agents: list[SettingsAgent]
    backups: list[BackupFile]
    storage: Storage


class RoadmapSnapshot(RoadmapVersion):
    """A saved revision of a roadmap: its title, overview and steps as they stood then."""

    title: str
    overview: str
    # The steps not retired then, each with its status then. plans._snapshot has written this
    # shape since the first version, and nothing rewrites a revision.
    steps: list[PlanStep]


class ReleaseRecord(Answer):
    """A release's own fields, as each read of it gives them."""

    id: str
    title: str
    description: str
    # A YYYY-MM-DD day; null while the release is not dated.
    releaseDate: str | None
    status: Literal["planned", "released", "cancelled"]
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    # Planned, with its date passed.
    pastDue: bool


class ReleaseCounts(Answer):
    """A listed release's active work items and the done ones, its open checks of each kind, and
    how many open checks are past due."""

    items: int
    itemsDone: int
    notify: int
    test: int
    prepare: int
    pastDue: int


class ReleaseListed(ReleaseRecord):
    counts: ReleaseCounts


class Releases(Answer):
    items: list[ReleaseListed]
    total: int
    limit: int
    offset: int


class ReleaseItem(Answer):
    """A work item a release holds."""

    id: str
    title: str
    status: Literal["open", "doing", "waiting", "blocked", "done"]
    archived: bool
    addedAt: str


class ReleaseRoadmap(Answer):
    """A roadmap a release holds, with its finished leaf steps out of all, as its progress bar counts them."""

    id: str
    title: str
    archived: bool
    addedAt: str
    steps: int
    stepsDone: int


class ReleaseStep(Answer):
    """A step a release holds, with its status as its roadmap shows it: a parent's follows its leaves."""

    id: str
    title: str
    # steps.status has no CHECK.
    status: str
    retired: bool
    roadmapId: str
    roadmapTitle: str
    roadmapArchived: bool
    addedAt: str


class ReleaseChange(Answer):
    """A PR or commit: one the release holds itself (direct), or one on a work item or roadmap it holds."""

    id: str
    kind: Literal["commit", "pr"]
    url: str
    reference: str
    title: str
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    # Null only if that work item or roadmap is missing, which `worklog doctor` reports.
    ownerTitle: str | None
    # The tracked chat that made it; all four are null when none did.
    chatId: str | None
    chatTitle: str | None
    provider: str | None
    model: str | None
    direct: bool
    # Archived itself, or on an archived work item or roadmap.
    archived: bool
    createdAt: str


class ReleaseHistoryEntry(Answer):
    """A change to a release or one of its checks, which entityId names, without its records:
    /history has those."""

    id: str
    entityId: str
    kind: str
    actorId: str
    actorLabel: str
    # events.actor_kind has no CHECK.
    actorKind: str
    createdAt: str


class Release(ReleaseRecord):
    """A release with what it holds, its changes, every check and who changed them when."""

    items: list[ReleaseItem]
    roadmaps: list[ReleaseRoadmap]
    steps: list[ReleaseStep]
    changes: list[ReleaseChange]
    # Every check, archived ones too, by due date (undated last), then as added.
    checks: list[ReleaseCheck]
    # The latest 100, newest first.
    history: list[ReleaseHistoryEntry]


class TicketWorkItem(Answer):
    """The work item a ticket names."""

    id: str
    title: str
    status: Literal["open", "doing", "waiting", "blocked", "done"]
    archived: bool


class TicketAssignee(Author):
    """Who a ticket is assigned to, as they stand now: the person, or an agent identity."""

    # Whether the agent identity holds a live credential; the person always does.
    active: bool


class TicketListed(Answer):
    """A ticket as lists show it: its description and resolution cut to 1,000 characters each."""

    id: str
    number: int
    key: str
    title: str
    description: str
    resolution: str
    # Whether either was cut; a ticket's own read cuts neither.
    truncated: bool
    kind: Literal["bug", "incident", "task"]
    priority: Literal["low", "normal", "high", "urgent"]
    status: Literal["open", "doing", "blocked", "done"]
    labels: list[str]
    repositoryIds: list[str]
    itemId: str | None
    # Null when the ticket names no work item, or one that is missing.
    item: TicketWorkItem | None
    assigneeId: str | None
    # Null when it is assigned to no one. An agent identity that is gone reads as "Unknown agent".
    assignee: TicketAssignee | None
    author: Author
    commentCount: int
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    closedAt: str | None
    lastActivityAt: str


class Tickets(Answer):
    items: list[TicketListed]
    total: int
    limit: int
    offset: int


class TicketComment(Answer):
    id: str
    ticketId: str
    text: str
    createdAt: str
    author: Author


class TicketComments(Answer):
    # The latest ones the read asked for (200 by default), oldest first; total counts them all.
    items: list[TicketComment]
    total: int


class Ticket(TicketListed):
    """A ticket whole, with its latest comments and who changed it when."""

    comments: TicketComments
    # The latest 100 changes, newest first, without their records: /history has those.
    history: list[Change]


class ProposalOperation(Answer):
    """One change a proposal makes, as its create or last update checked it."""

    # An action operations.py defined when the proposal was written.
    action: str
    payload: dict[str, Any]


class ProposalListed(Answer):
    """A proposal as GET /proposals and the context list it: whole, without its evidence files."""

    id: str
    title: str
    rationale: str
    # proposals.status has no CHECK.
    status: str
    # Applied when its agent submitted it, because approval was off.
    autoApproved: bool
    revision: int
    actorId: str
    actorLabel: str
    # The directory its agent identity registered from; null for the person, a credential issued
    # in Settings, or an identity that registered without one.
    actorDirectory: str | None
    operations: list[ProposalOperation]
    supersedesId: str | None
    createdAt: str
    updatedAt: str
    # Null until it is first submitted, and for a draft.
    submittedAt: str | None
    # Always empty here: a proposal's own read lists its evidence files.
    attachments: list[Attachment]


class Proposals(Answer):
    items: list[ProposalListed]
    total: int


class Proposal(ProposalListed):
    """A proposal as its own read returns it, with its evidence files."""

    # Always empty: notes belong to work items, roadmaps and steps.
    notes: list[Note]


class ProposalEntry(Answer):
    """A proposal as a history lists it, the latest first, with the number of its changes."""

    # Its place in the sequence, 1 the first.
    number: int
    id: str
    title: str
    # proposals.status has no CHECK.
    status: str
    autoApproved: bool
    actorLabel: str
    # The agent identity of the credential that made it; null for the person's.
    agentId: str | None
    createdAt: str
    # Null for a draft and for one never submitted.
    submittedAt: str | None
    # Null unless it is approved.
    appliedAt: str | None
    updatedAt: str
    operationCount: int


class AgentProposals(Answer):
    items: list[ProposalEntry]
    total: int


class AppliedChange(Answer):
    """One change of an applied proposal that was made to the record, by its number in the proposal."""

    number: int
    action: str


class AppliedProposal(ProposalEntry):
    changes: list[AppliedChange]


class AppliedProposals(Answer):
    items: list[AppliedProposal]
    total: int
