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

from pydantic import BaseModel, ConfigDict, Field


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


class ActiveLeaseSummary(Answer):
    """Compact summary of an active or offered checkout lease."""

    leaseId: str
    agentId: str | None
    holderLabel: str
    provider: str
    slotIndex: int | None
    branch: str
    worktreePath: str
    mode: Literal["shared", "exclusive"]
    state: Literal["offered", "active"]
    expiresAt: str | None


class RepositorySlotSummary(Answer):
    """Compact checkout slot occupancy and policy flags for lists and badges."""

    maxActiveLeases: int
    activeCount: int
    queuedCount: int
    dirtyCount: int
    rescuedCount: int
    baseReadonly: bool
    configured: bool
    # The slot policy's revision, 0 until configured: a change from a list, such as the slot count,
    # carries it as the policy's own read would.
    revision: int


class OverlapSlotPolicy(Answer):
    maxActiveLeases: int
    activeCount: int
    queuedCount: int
    dirtyCount: int
    baseReadonly: bool
    configured: bool


class SlotPolicy(Answer):
    """A repository's checkout slot policy (or effective defaults when unconfigured)."""

    repositoryId: str
    maxActiveLeases: int
    defaultTtlSeconds: int
    baseCheckoutPath: str
    slotRootPath: str
    defaultBranch: str
    baseReadonly: bool
    autoRescueDirty: bool
    revision: int
    updatedAt: str | None
    configured: bool


class BaseLockResult(Answer):
    """Result of locking or unlocking a repository's canonical base checkout."""

    repositoryId: str
    basePath: str
    baseReadonly: bool
    filesAffected: int
    detachedAt: str
    policy: SlotPolicy


class Lease(Answer):
    """A bounded repository checkout slot lease (active, offered, queued, or closed)."""

    id: str
    repositoryId: str
    repositoryName: str
    agentId: str | None
    credentialId: str | None
    runId: str | None
    provider: str
    chatId: str
    holderLabel: str
    slotIndex: int | None
    worktreePath: str
    branch: str
    mode: Literal["shared", "exclusive"]
    state: Literal["queued", "offered", "active", "released", "expired", "expired_dirty", "revoked", "cancelled"]
    priority: Literal["normal", "pr_fixup", "wave", "incident"]
    queueSeq: int
    queuePosition: int | None
    reason: str
    requestedAt: str
    offeredAt: str | None
    acquiredAt: str | None
    lastHeartbeatAt: str
    expiresAt: str | None
    releasedAt: str | None
    releaseReason: str | None
    rescueRef: str
    revision: int
    fetchWarning: str


class Leases(Answer):
    items: list[Lease]
    total: int
    limit: int
    offset: int


class RepositorySlot(Answer):
    slotIndex: int
    expectedWorktreePath: str
    state: Literal["free", "offered", "active", "expired_dirty"]
    lease: Lease | None


class RepositorySlots(Answer):
    repositoryId: str
    repositoryName: str
    policy: SlotPolicy
    activeCount: int
    queuedCount: int
    dirtyCount: int
    availableSlots: int
    slots: list[RepositorySlot]
    activeLeases: list[Lease]
    queuedLeases: list[Lease]
    recentLeases: list[Lease]


class DispatchItem(Answer):
    """A durable dispatch-time session queue entry linked 1:1 to a repository lease."""

    id: str
    itemId: str
    itemTitle: str
    repositoryId: str
    repositoryName: str
    leaseId: str
    provider: Literal["codex", "claude", "antigravity"]
    requestedBranch: str
    branch: str
    slotIndex: int | None
    worktreePath: str
    queuePosition: int | None
    status: Literal["queued", "provisioning", "preparing", "ready", "ready_manual", "failed", "cancelled"]
    sessionId: str | None
    chatRecordId: str | None
    model: str
    message: str
    copyablePrompt: str | None
    revision: int
    createdAt: str
    updatedAt: str


class DispatchQueue(Answer):
    items: list[DispatchItem]
    total: int
    limit: int
    offset: int


class SessionLaunch(Answer):
    id: str
    provider: Literal["codex", "claude", "antigravity"]
    status: Literal["queued", "provisioning", "starting", "preparing", "ready", "ready_manual", "failed", "cancelled"]
    chat: TrackedChat | None
    message: str
    # A provider chat can exist before a failed local link is repaired.
    sessionId: str | None = None
    dispatchId: str | None = None
    repositoryId: str | None = None
    repositoryName: str | None = None
    leaseId: str | None = None
    slotIndex: int | None = None
    worktreePath: str | None = None
    branch: str | None = None
    queuePosition: int | None = None
    activeHolders: list[ActiveLeaseSummary] | None = None
    copyablePrompt: str | None = None


class AgentChatOpened(Answer):
    """The person's handoff of a pending request to the agent its slot waits on: whether Worklog asked
    the agent's desktop app to open its chat, why not, and the message for the person to paste there."""
    messageId: str
    targetId: str
    agentId: str
    agentLabel: str
    chatId: str | None
    directory: str | None
    provider: Literal["codex", "claude"] | None
    sessionId: str | None
    opened: bool
    message: str
    paste: str


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
    submittedBy: str
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


class ResourcePage(ResourceListed):
    """A resource with its page's text, as its commands return it."""

    body: str


class Resource(ResourcePage):
    history: list[Change]


class Author(Answer):
    """Who wrote a ticket or a ticket's comment, as written then, with the avatar their agent identity
    has now."""

    id: str
    label: str
    kind: Literal["agent", "human"]
    # Null for the person, and for an agent identity with none or that is gone.
    avatar: str | None


class RecordChange(Answer):
    """One entry of a record's history: who made the change, its kind and when."""

    id: str
    entityId: str
    kind: str
    actorId: str
    actorLabel: str
    actorKind: str
    createdAt: str
    localDate: str


class Event(RecordChange):
    """One entry of a record's history in full: the record before and after the change, as written then."""

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
    # Emptied by compact: the evidence of a proposal withdrawn or rejected long ago (step 2.6). Its
    # name, size and hash stay; its download answers 410.
    removed: bool
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


class PullRequest(ListedGithubReference):
    ownerTitle: str


class PullRequests(Answer):
    items: list[PullRequest]
    total: int
    limit: int
    offset: int


class ActivityWindow(Answer):
    """The days the activity covers, both ends included, in Worklog's timezone."""

    days: int
    # Answered as `from`, which Python keeps for itself.
    from_: str = Field(alias="from")
    to: str
    timezone: str


class ActivityDay(Answer):
    """One day on the default branches: commits (merge commits left out), the lines they added and
    removed, and the pull request merges among the branches' own commits."""

    day: str
    commits: int
    additions: int
    deletions: int
    merged: int


class ActivityRepository(Answer):
    """A repository's totals in the window, and how its last count went (git_activity.py)."""

    id: str
    name: str
    # The default branch counted, `main` or `master`; empty until a count reaches one.
    branch: str
    state: Literal["pending", "scanning", "ready", "no_checkout", "checkout_mismatch", "no_default_branch",
                   "failed", "timed_out"]
    detail: str
    # When the checkout last fetched (FETCH_HEAD), and when Worklog last counted it.
    fetchedAt: str | None
    scannedAt: str | None
    commits: int
    additions: int
    deletions: int
    merged: int
    # Registered pull requests in this repository, merged or not.
    registered: int


class ActivityPullRequest(Answer):
    """A registered pull request's merge on its repository's default branch; nulls while none is found.
    A merge commit's size waits for the next count after it is registered."""

    id: str
    repositoryId: str | None
    number: int | None
    mergedAt: str | None
    sha: str | None
    additions: int | None
    deletions: int | None
    files: int | None


class PullRequestActivity(Answer):
    """GET /pull-requests/activity: what reached each repository's default branch, read from its local
    checkout without fetching."""

    window: ActivityWindow
    daily: list[ActivityDay]
    repositories: list[ActivityRepository]
    pullRequests: list[ActivityPullRequest]
    # A count is under way: `scanned` of `total` repositories are done.
    scanning: bool
    scanned: int
    total: int


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
    references: list[FileReference]
    people: list[RecordPerson]
    # The calculated Active status (agent_activity): an agent reported work on it within two hours
    # while its status is open or doing. Worked out on each read and never stored.
    agentActive: bool
    dailyEntries: list[DailyEntry]
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
    wipCap: int | None
    tier: int | None
    archived: bool
    # Null when no registrant is recorded: coordination.repository_record gives none for an empty kind.
    registeredBy: RegisteredBy | None
    reviewed: bool
    revision: int
    createdAt: str
    updatedAt: str


class DiscussedRepository(Repository):
    """A repository with its own discussion's id (v22), slot summary (v34), and direct catalog dependencies (v35)."""

    # Null for a merged repository, whose discussion is tagged with the survivor now, and for one
    # archived before v22 and not recovered since (coordination.with_discussion).
    discussionId: str | None
    upstreamIds: list[str]
    downstreamIds: list[str]
    slotSummary: RepositorySlotSummary


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


class PersonRecord(Answer):
    """A person's own fields, as their commands return them."""

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


class Person(PersonRecord):
    """A person with what they are tied to and their history."""

    roadmaps: list[PersonRoadmap]
    workItems: list[PersonWorkItem]
    repositories: list[PersonRepository]


class PlanStep(Answer):
    """A step's own fields, as a roadmap's steps and its saved revisions give them. A parent's
    status is derived from its leaves."""

    id: str
    title: str
    description: str
    # What the step's work produces. A revision saved before steps had outcomes gives none.
    outcome: str
    parentId: str | None
    position: int
    # steps.status has no CHECK.
    status: str
    revision: int
    retired: bool
    workClass: str
    repositoryIds: list[str]
    waveIds: list[str]


class RoadmapStep(PlanStep):
    """A step as its roadmap lists it, with its notes and files."""

    notes: list[Note]
    attachments: list[Attachment]
    # The calculated Active status (agent_activity): its status, a parent's derived, is open or doing,
    # and an agent reported work within two hours on it, on a work item on it, or on a step under it
    # that shows Active. Never for a retired step. Worked out on each read and never stored.
    agentActive: bool


class RoadmapVersion(Answer):
    number: int
    createdAt: str
    note: str


class FamilyRoadmap(Answer):
    """A roadmap above or below another, as that one lists it."""

    id: str
    title: str
    archived: bool


class RoadmapWorkItem(WorkItemRecord):
    """A work item on the roadmap or one of its steps, with the people tied to it."""

    people: list[RecordPerson]
    # The calculated Active status (agent_activity): an agent reported work on it within two hours
    # while its status is open or doing. Worked out on each read and never stored.
    agentActive: bool


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
    ancestors: list[FamilyRoadmap]
    children: list[FamilyRoadmap]
    workItems: list[RoadmapWorkItem]
    people: list[RecordPerson]


class NearbyStep(Answer):
    """A step above or beside another, by its title and status."""

    id: str
    title: str
    # steps.status has no CHECK; a parent's is derived from its leaves.
    status: str


class SiblingStep(NearbyStep):
    position: int


class Step(RoadmapStep):
    """A step as its own read returns it: with its roadmap, retired or not, where it sits in the plan,
    and the tracked chats on it."""

    roadmapId: str
    # The steps above it, from the top of the plan down; none for a top-level step.
    ancestors: list[NearbyStep]
    # The other steps under its parent, retired ones aside, in outline order.
    siblings: list[SiblingStep]
    # The roadmap's tracked chats assigned to it, archived ones included, in the order they were put there.
    chats: list[TrackedChat]


class DoneStep(Answer):
    """A step marked done, and when it last was."""

    id: str
    title: str
    doneAt: str


class RoadmapBoard(Answer):
    """What a roadmap's status board reads besides the roadmap itself."""

    # The submitted proposals that would change it or what it holds, oldest first.
    waiting: list[OpenProposal]
    # Its steps in the plan marked done, the latest first; at most 10.
    recentlyDone: list[DoneStep]


class OutlineStep(Answer):
    """A step as a roadmap's outline gives it: no text."""

    id: str
    title: str
    # steps.status has no CHECK; a parent's is derived from its leaves.
    status: str
    parentId: str | None
    position: int
    workClass: str
    repositoryIds: list[str]
    waveId: str | None


class RoadmapOutline(Answer):
    """A roadmap's plan without its text: each step in it, retired ones aside, depth-first in outline
    order, each before its children."""

    id: str
    title: str
    archived: bool
    revision: int
    steps: list[OutlineStep]


class History(Answer):
    items: list[Event]
    total: int


class TabCounts(Answer):
    """What a work item's tabs on Today hold, so they say so before the item is read."""

    links: int
    files: int
    scratchpad: bool


class DayItem(WorkItemRecord):
    rank: int | None
    # The calculated Active status (agent_activity): an agent reported work on it within two hours
    # while its status is open or doing. Worked out on each read and never stored.
    agentActive: bool


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
    entries: list[DayEntry]
    summary: DaySummary
    nextItemId: str | None


class PersonListed(PersonRecord):
    """A person as the people list shows them, with how many active records each kind ties them to."""

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


class WorkItemListed(WorkItemRecord):
    rank: int | None
    isBacklog: bool
    people: list[RecordPerson]
    # The calculated Active status (agent_activity): an agent reported work on it within two hours
    # while its status is open or doing. Worked out on each read and never stored.
    agentActive: bool


class WorkItems(Answer):
    items: list[WorkItemListed]
    total: int
    rankedCount: int
    # The backlog's revision, which a reorder checks.
    collectionRevision: int


class PlanRoadmap(Answer):
    """A roadmap's own fields as plans.list gives them, which the context lists."""

    id: str
    title: str
    overview: str
    parentId: str | None
    revision: int
    archived: bool
    createdAt: str
    updatedAt: str


class RoadmapListed(PlanRoadmap):
    people: list[RecordPerson]
    # Its questions still open, archived ones aside.
    openQuestions: int


class Roadmaps(Answer):
    items: list[RoadmapListed]
    total: int


class PlanRoadmaps(Answer):
    items: list[PlanRoadmap]
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
    """A record's latest history entries the reader may see: each change without its before and after,
    unless one change was asked for by its ID or every one with payloads."""

    items: list[Event | RecordChange]


class Attachments(Answer):
    items: list[Attachment]
    total: int


class RecordSummary(Answer):
    """A record by its title, and whether it is archived."""

    id: str
    title: str
    archived: bool


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


class ListedBackup(BackupFile):
    """A backup as Settings lists it (step 2.2)."""

    # manual, daily, pre-migration, pre-restore, pre-compact or pre-restore-raw, from the file's name.
    kind: str | None
    # Marked by the person to keep: the rule for manual backups never removes it.
    kept: bool
    # The rule for manual backups would remove it (recovery.manual_prunable).
    prunable: bool


class BackupKind(Answer):
    count: int
    bytes: int


class BackupPruning(Answer):
    """The rule for manual backups: the newest `keep`, and those marked to keep. Until the person
    confirms it once, nothing is removed, and `candidates` lists what the rule would remove."""

    keep: int
    confirmed: bool
    candidates: list[str]
    candidateBytes: int


class Storage(Answer):
    databaseBytes: int
    backupBytes: int
    backupCount: int
    # Count and bytes of each kind of backup, by kind.
    backupKinds: dict[str, BackupKind]
    # Free bytes on the disk that holds the data folder.
    diskFreeBytes: int
    pruning: BackupPruning
    # Null unless the last automatic backup failed, the database is past 2 GB, the backups pass
    # BACKUP_RATIO times the database, or the disk's free space falls below twice the database.
    warning: str | None


class BackupsPruned(Answer):
    """What confirming the rule for manual backups removed."""

    removed: list[str]


class BackupMark(Answer):
    name: str
    kept: bool


class Settings(Answer):
    timezone: str
    today: str
    version: str
    requireProposalApproval: bool
    agentRepositoryRegistration: bool
    credentials: list[SettingsCredential]
    agents: list[SettingsAgent]
    backups: list[ListedBackup]
    storage: Storage


class RoadmapSnapshot(RoadmapVersion):
    """A saved revision of a roadmap: its title, overview and steps as they stood then."""

    title: str
    overview: str
    # The steps not retired then, each with its status then. plans._snapshot has written this
    # shape since the first version, and nothing rewrites a revision.
    steps: list[PlanStep]


class TicketWorkItem(Answer):
    """The work item a ticket names."""

    id: str
    title: str
    status: Literal["open", "doing", "waiting", "blocked", "done"]
    archived: bool


class Participant(Author):
    """The person or an agent identity as they stand now: who a ticket is assigned to, or who is
    on the records an overlap check names."""

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
    assignee: Participant | None
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
    """A proposal as its writes answer it: whole, without its evidence files."""

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


class ProposalSummary(Answer):
    """A proposal as GET /proposals lists it: who made it, its state and dates, and the number of its
    changes. Its rationale, changes and evidence come from its own read."""

    id: str
    title: str
    # proposals.status has no CHECK.
    status: str
    revision: int
    # Applied when its agent submitted it, because approval was off.
    autoApproved: bool
    supersedesId: str | None
    actorId: str
    actorLabel: str
    # The directory its agent identity registered from; null for the person, a credential issued
    # in Settings, or an identity that registered without one.
    actorDirectory: str | None
    createdAt: str
    # Null until it is first submitted, and for a draft.
    submittedAt: str | None
    updatedAt: str
    # How many changes it holds; 0 for stored operations that are not a list.
    operationCount: int


class Proposals(Answer):
    items: list[ProposalSummary]
    total: int


class OpenProposal(Answer):
    """An open proposal as the context lists it (K18): a draft the caller may see, or one submitted
    for review, without its changes, which `proposal show` reads."""

    id: str
    title: str
    # draft or submitted.
    status: str
    revision: int
    actorLabel: str
    # The agent identity of the credential that made it; null for the person's.
    agentId: str | None
    createdAt: str
    # Null for a draft.
    submittedAt: str | None
    updatedAt: str
    operationCount: int


class OpenProposals(Answer):
    items: list[OpenProposal]
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


class AgentRun(Answer):
    """A run: one stretch of an agent's work under one credential, as it reported it."""

    id: str
    agentId: str
    credentialId: str
    clientRunKey: str
    displayName: str
    # Empty when the run named none.
    provider: str
    # The chat it reported, or the one its identity registered with; null when neither names one.
    externalChatId: str | None
    revision: int
    createdAt: str
    updatedAt: str
    # Both null while the run is open. agent_runs.end_reason has no CHECK.
    endedAt: str | None
    endReason: str | None


class AgentRuns(Answer):
    items: list[AgentRun]
    total: int
    limit: int
    offset: int


class ParticipationRepository(Answer):
    """A repository a participation reports working in, and where."""

    repositoryId: str
    repositoryName: str
    archived: bool
    checkoutPath: str
    worktreePath: str
    branch: str


class Participation(Answer):
    """What a run reports working on: a work item, step, or none, and the repositories it works in."""

    id: str
    runId: str
    itemId: str | None
    stepId: str | None
    description: str
    contexts: list[ParticipationRepository]
    revision: int
    createdAt: str
    updatedAt: str
    lastSeenAt: str
    # Both null while it is held. participations.release_reason has no CHECK.
    releasedAt: str | None
    releaseReason: str | None
    # Not seen for two hours.
    stale: bool
    run: AgentRun


class Participations(Answer):
    items: list[Participation]
    total: int
    limit: int
    offset: int


class CoordinationUpdate(Answer):
    """A status update a run posted on its participation."""

    id: str
    participationId: str
    runId: str
    agentId: str
    credentialId: str
    # The run's display name when it posted.
    authorLabel: str
    kind: Literal["update", "blocker", "handoff"]
    text: str
    itemId: str | None
    # The participation's contexts when it posted, as coordination.post_update wrote them.
    contexts: list[ParticipationRepository]
    createdAt: str


class CoordinationUpdates(Answer):
    items: list[CoordinationUpdate]
    total: int
    limit: int
    offset: int


class RegistryWorkItem(Answer):
    """A work item as the registry names it, with the roadmap its step, or it, sits on."""

    id: str
    title: str
    status: Literal["open", "doing", "waiting", "blocked", "done"]
    archived: bool
    roadmapId: str | None


class AgentWork(Answer):
    """Open participation an agent reports: the work, step, and the repositories it is at now."""

    participationId: str
    runId: str
    runName: str
    description: str
    lastSeenAt: str
    # Not seen for two hours.
    stale: bool
    item: RegistryWorkItem | None
    stepId: str | None
    roadmap: RecordSummary | None
    contexts: list[ParticipationRepository]


class AgentRole(Answer):
    """A responsibility role an agent owns, with the roadmap it sits within."""

    id: str
    label: str
    contextType: Literal["item", "roadmap", "step"]
    contextId: str
    # The record's title; its id when the record is missing.
    contextTitle: str
    roadmap: RecordSummary | None
    updatedAt: str


class AgentListed(Answer):
    """An agent identity, gathered from what it wrote: what its card on the Agents page and its
    participant profile both show."""

    id: str
    label: str
    # Whether it holds a live credential.
    active: bool
    createdAt: str
    # The latest of its runs, check-ins, updates and messages; null before any.
    lastActivityAt: str | None
    providers: list[str]
    openRuns: int
    runCount: int
    messageCount: int
    working: list[AgentWork]
    roles: list[AgentRole]


class AgentCredential(Answer):
    id: str
    label: str
    createdAt: str
    revoked: bool
    # Null for one issued in Settings.
    registeredBy: CredentialSponsor | None
    # The chat its registration named; null when it named none.
    chatId: str | None


class TiedChat(Answer):
    """A tracked chat whose chat ID the agent reported."""

    id: str
    chatId: str
    title: str
    provider: str
    model: str
    archived: bool
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    ownerTitle: str


class AgentRunWithChats(AgentRun):
    """A run with the tracked chats that carry the chat ID it reported."""

    chats: list[TiedChat]


class AgentRunsListed(Answer):
    # The latest 200; total counts them all.
    items: list[AgentRunWithChats]
    total: int


TieReason = Literal["participation", "role", "update", "message", "run", "chat"]


class TiedWorkItem(Answer):
    id: str
    title: str
    status: Literal["open", "doing", "waiting", "blocked", "done"]
    archived: bool
    roadmap: RecordSummary | None
    # Every reason that ties the agent to it, in display order.
    via: list[TieReason]
    # Tied by something still open: participation, a role, an open run.
    current: bool
    lastAt: str | None


class TiedRoadmap(RecordSummary):
    via: list[TieReason]
    current: bool
    lastAt: str | None


class TiedRepository(Answer):
    id: str
    name: str
    archived: bool
    via: list[TieReason]
    current: bool
    lastAt: str | None


class TiedChatEntry(TiedChat):
    via: list[TieReason]
    current: bool
    lastAt: str | None


class TiedWorkItems(Answer):
    # Current ties first, then the latest; the first 200, and total counts them all.
    items: list[TiedWorkItem]
    total: int


class TiedRoadmaps(Answer):
    items: list[TiedRoadmap]
    total: int


class TiedRepositories(Answer):
    items: list[TiedRepository]
    total: int


class TiedChats(Answer):
    items: list[TiedChatEntry]
    total: int


class AgentCard(AgentListed):
    """An agent identity as its card on the Agents page shows it: who it is, and the roadmaps and
    repositories its work ties it to, as its own page lists them. The person's alone, like the
    page: tracked chats tie it too (AgentProfile, which any caller reads, holds none of this)."""

    avatar: str | None
    roadmaps: TiedRoadmaps
    repositories: TiedRepositories


class Agents(Answer):
    items: list[AgentCard]
    total: int
    limit: int
    offset: int


class Agent(AgentCard):
    """An agent identity with its credentials, its runs and what it is tied to, and why."""

    credentials: list[AgentCredential]
    runs: AgentRunsListed
    workItems: TiedWorkItems
    chats: TiedChats


class MessageActivity(Answer):
    """A discussion message the agent posted."""

    kind: Literal["message"]
    id: str
    createdAt: str
    # The first 1,000 characters.
    text: str
    truncated: bool
    discussionId: str
    discussionTitle: str
    # The chat it reported; null when it named none.
    chatId: str | None
    # The tracked chats that carry that chat ID.
    chats: list[Named]


class UpdateActivity(Answer):
    """A status update one of the agent's runs posted."""

    kind: Literal["update"]
    id: str
    createdAt: str
    # The first 1,000 characters.
    text: str
    truncated: bool
    updateKind: Literal["update", "blocker", "handoff"]
    runName: str
    participationId: str
    item: Named | None
    contexts: list[ParticipationRepository]


class AgentActivity(Answer):
    # The newest first.
    items: list[MessageActivity | UpdateActivity]
    total: int
    limit: int
    offset: int


class RoleName(Answer):
    id: str
    label: str


class RoadmapTie(Answer):
    """What ties a worker to a roadmap's step now, or to the roadmap itself with a null stepId."""

    stepId: str | None
    via: Literal["participation", "role", "chat"]
    item: Named | None
    chat: Named | None
    role: RoleName | None
    lastAt: str | None
    stale: bool


class RoadmapWorker(Answer):
    """An agent at work on the roadmap, or a tracked chat that stands for itself."""

    kind: Literal["agent", "chat"]
    id: str
    label: str
    # An agent identity's avatar; null for a chat.
    avatar: str | None
    # A chat's provider and model; empty for an agent.
    provider: str
    model: str
    # The latest first.
    ties: list[RoadmapTie]
    lastAt: str | None
    # Every tie is stale.
    stale: bool


class RosterStep(Answer):
    """A step still in the plan that an agent's work ties it to, with why and when."""

    stepId: str
    via: list[TieReason]
    current: bool
    lastAt: str | None


class RosterAgent(Answer):
    """An agent whose work ties it to the roadmap, now or before: one whose card names the roadmap."""

    id: str
    label: str
    avatar: str | None
    # Its credential is live.
    active: bool
    # Every reason, across the roadmap and its steps, in display order.
    via: list[TieReason]
    # Tied by something still open: participation, a role, an open run.
    current: bool
    lastAt: str | None
    # Current ties first, then the latest. Work with no step, or on a retired one, ties no step.
    steps: list[RosterStep]


class RoadmapRoster(Answer):
    # Current ties first, then the latest; the first 200, and total counts them all.
    items: list[RosterAgent]
    total: int


class RoadmapAgents(Answer):
    # Fresh work first, then the latest.
    items: list[RoadmapWorker]
    # Every agent whose work ties it to the roadmap, now or before.
    roster: RoadmapRoster


class OverlapRepository(Answer):
    id: str
    name: str
    # Its own discussion; null for one merged into another or archived before v22.
    discussionId: str | None
    slotPolicy: OverlapSlotPolicy
    activeLeases: list[ActiveLeaseSummary]


class OverlapWorkItem(Answer):
    id: str
    title: str
    status: Literal["open", "doing", "waiting", "blocked", "done"]


class OverlapParticipation(Answer):
    """Another agent's open participation in the repositories, on the work items, or on work on the steps."""

    participationId: str
    # Null if the agent identity is gone.
    agent: Participant | None
    runId: str
    runName: str
    description: str
    lastSeenAt: str
    stale: bool
    item: OverlapWorkItem | None
    stepId: str | None
    contexts: list[ParticipationRepository]


class Announcement(Answer):
    """A message someone else posted in a repository's own discussion, the opening one aside."""

    repositoryId: str
    discussionId: str
    messageId: str
    # As the author stands now; null if their agent identity is gone.
    author: Participant | None
    authorId: str
    authorLabel: str
    # Whether the author reports work in that repository now.
    authorReporting: bool
    createdAt: str
    # The first 1,000 characters.
    text: str
    truncated: bool


class OverlapTag(Answer):
    """One of the named records a thread is tagged with."""

    type: Literal["repository", "item", "step", "ticket"]
    id: str


class OverlapDiscussion(Answer):
    """Another open thread tagged with a named record, where someone besides the caller wrote."""

    id: str
    title: str
    pinned: bool
    updatedAt: str
    matched: list[OverlapTag]


class OverlapRole(Answer):
    id: str
    label: str
    contextType: Literal["item", "step"]
    contextId: str
    owner: Participant | None


class OverlapTicket(Answer):
    id: str
    number: int
    title: str
    status: Literal["open", "doing", "blocked", "done"]
    assignee: Participant | None


class FrozenWaveRef(Answer):
    waveId: str
    waveName: str
    roadmapId: str
    freezeStatus: Literal["open", "batching", "frozen"]
    frozenRef: str


class PendingRepinRef(Answer):
    waveId: str
    waveName: str
    upstreamRepoId: str
    upstreamRepoName: str
    consumerRepoId: str
    consumerRepoName: str
    status: Literal["pending", "in_progress", "verified", "waived"]


class RepoAdvisory(Answer):
    repositoryId: str
    repositoryName: str
    wipCap: int | None
    effectiveWipCap: int | None
    tier: int | None
    activeWriters: int
    doingSteps: int
    overWipCap: bool
    frozenWaves: list[FrozenWaveRef]
    pendingRepins: list[PendingRepinRef]


class MultiRepoStepRef(Answer):
    stepId: str
    stepTitle: str
    roadmapId: str
    repositoryIds: list[str]


class CompetingWaveStepRef(Answer):
    stepId: str
    stepTitle: str
    roadmapId: str
    activeWaveId: str
    activeWaveName: str


class DispatchAdvisories(Answer):
    """Execution guardrails for overlaps and proposal review: WIP caps, freezes, re-pins, and multi-repo leaves."""

    repositories: list[RepoAdvisory]
    multiRepoSteps: list[MultiRepoStepRef]
    competingWaveSteps: list[CompetingWaveStepRef]


class Overlaps(Answer):
    """Who else is on the named records and what they said there, leaving out the caller's own."""

    repositories: list[OverlapRepository]
    participations: list[OverlapParticipation]
    announcements: list[Announcement]
    discussions: list[OverlapDiscussion]
    roles: list[OverlapRole]
    tickets: list[OverlapTicket]
    dispatchAdvisories: DispatchAdvisories


class AgentOpenRun(Answer):
    """An open run as a participant's profile shows it, with the chat it reports."""

    id: str
    displayName: str
    provider: str
    chatId: str | None
    createdAt: str


class ParticipationOutput(Answer):
    """A pull request or commit a participation reported producing (v33)."""

    id: str
    participationId: str
    repositoryId: str
    repositoryName: str
    kind: Literal["pr", "commit"]
    # A pull request's number, or a commit's lowercased SHA.
    reference: str
    # A pull request's address as reported, without query or fragment; empty for a bare number or a commit.
    url: str
    createdAt: str


class AgentProfile(AgentListed):
    """An agent identity as a participant's profile shows it, to any caller: who it is, and since v33
    where to reach it (decision R11)."""

    kind: Literal["agent"]
    avatar: str | None
    # The chat it works in: the provider's session or thread ID its newest registration named.
    chatId: str | None
    # The directory it registered from, its project folder; null when it named none.
    directory: str | None
    registeredAt: str | None
    # Its latest run's provider; empty when none named one.
    provider: str
    # Nothing heard from it for two hours, or ever.
    stale: bool
    runs: list[AgentOpenRun]
    # What its participation reported producing, newest first, at most 50.
    outputs: list[ParticipationOutput]


class ParticipationOutputsReported(Answer):
    participation: Participation
    # Everything the participation reported producing, oldest first.
    outputs: list[ParticipationOutput]
    # How many this report added; a repeat adds none.
    added: int


class Actor(Answer):
    """The person or an agent identity, by the label it has now."""

    id: str
    label: str
    kind: Literal["human", "agent"]


class Participants(Answer):
    """Everyone who can take part in a discussion: the person first, then each agent identity."""

    items: list[Participant]
    # The caller.
    self: Actor


class OwnerMatch(Answer):
    """A record the reference names."""

    kind: Literal["ticket", "item", "pr", "commit", "branch", "path", "agent"]
    id: str
    label: str
    matchedBy: Literal["key", "number", "id", "url", "sha", "branch", "path"]


class OutputNamed(Answer):
    kind: Literal["pr", "commit"]
    reference: str
    url: str


class OwnerRelation(Answer):
    """One thing that ties an agent to what the reference names, with its evidence; fields that don't
    apply to the relation are null."""

    relation: Literal["assignee", "role", "producer", "branch", "checkout", "agent", "participant", "chat", "author",
                      "commenter", "directory"]
    # Whether this relation makes the agent an owner.
    owner: bool
    # When it last held, and whether something still open holds it.
    at: str | None
    current: bool
    ticketId: str | None
    roleId: str | None
    roleLabel: str | None
    participationId: str | None
    repositoryId: str | None
    repositoryName: str | None
    branch: str | None
    checkoutPath: str | None
    worktreePath: str | None
    chatId: str | None
    chatTitle: str | None
    githubReferenceId: str | None
    output: OutputNamed | None
    directory: str | None


class OwnerAgent(AgentProfile):
    """An agent tied to what the reference names: its profile, whether it owns it, and why."""

    owner: bool
    relations: list[OwnerRelation]


class Owners(Answer):
    """Who owns what a reference names (v33): the records it matched, the person, and the agents tied
    there, owners first."""

    ref: str
    matches: list[OwnerMatch]
    # The person: who approves, reassigns, and receives escalations.
    person: Actor
    agents: list[OwnerAgent]


class AvatarOption(Answer):
    name: str
    # Whether no other agent identity shows it now.
    available: bool


class Avatars(Answer):
    items: list[AvatarOption]
    # The least-used avatar, chosen afresh on each read.
    suggested: str
    # The calling agent identity's own; null for the person and for an identity with none.
    current: str | None


class RoleOfferOpen(Answer):
    """A role's pending offer to another agent identity."""

    id: str
    fromId: str
    toId: str
    toLabel: str
    note: str


class DiscussionRole(Answer):
    """A responsibility role on a work item, roadmap or step, with its owner and any pending offer."""

    id: str
    contextType: Literal["item", "roadmap", "step"]
    contextId: str
    label: str
    # The record's title; its id when the record is missing.
    contextTitle: str
    # Null while no agent identity owns it.
    ownerId: str | None
    ownerLabel: str | None
    revision: int
    # Counts its owners: a message sent on its behalf names the generation that sent it.
    generation: int
    offer: RoleOfferOpen | None


class DiscussionRoles(Answer):
    items: list[DiscussionRole]
    total: int
    limit: int
    offset: int


TagKind = Literal["item", "roadmap", "step", "repository", "ticket", "person", "resource", "question", "decision"]


class ContextTag(Answer):
    """A record a discussion is tagged with, or that a role in it brings in, as record_kinds.summary
    gives it, with who added the tag and when. A missing record has a null title."""

    type: TagKind
    id: str
    title: str | None
    archived: bool
    # Whether it makes the thread its repository's own discussion.
    home: bool
    # Both null for a context a role brings in with no tag of its own. addedBy is null too when
    # the agent identity that added it is gone.
    addedBy: Actor | None
    addedAt: str | None


class StatusContextTag(ContextTag):
    """A work item's tag."""

    status: str


class StepContextTag(StatusContextTag):
    """A step's or a question's tag, with the roadmap it opens on."""

    roadmapId: str
    roadmapTitle: str


class DecisionContextTag(ContextTag):
    roadmapId: str
    roadmapTitle: str


class RepositoryContextTag(ContextTag):
    # The repository it was merged into; null while it stands on its own.
    mergedInto: str | None


class TicketContextTag(StatusContextTag):
    number: int


# One shape per kind of record, told apart by their keys, since each model forbids the keys it doesn't name.
DescribedContext = (ContextTag | StatusContextTag | StepContextTag | DecisionContextTag | RepositoryContextTag
                    | TicketContextTag)


class HomeRepository(Answer):
    """The repository whose own discussion this is."""

    id: str
    # Null only if that repository is missing.
    name: str | None


class DiscussionSummary(Answer):
    """A discussion as lists show it: its tags and how many acknowledgement requests are pending."""

    id: str
    title: str
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    pinned: bool
    pinnedAt: str | None
    contexts: list[DescribedContext]
    # Free-text repository labels from before the catalog, read only (K8).
    repos: list[str]
    pendingCount: int
    # Null unless it is a repository's own discussion.
    repository: HomeRepository | None


class Discussions(Answer):
    # Pinned first, the latest pinned first, then the most recently active.
    items: list[DiscussionSummary]
    total: int
    limit: int
    offset: int


class Presence(Answer):
    """Whether an agent can be reached, as its own reports say."""

    active: bool
    lastSeenAt: str | None
    # Nothing heard for two hours, or ever.
    stale: bool
    openRuns: int


class AcknowledgementTarget(Answer):
    """One slot of an acknowledgement request: a named participant, a role, or any agent or anyone."""

    id: str
    kind: Literal["agent", "human", "role", "open_agent", "open_any"]
    # Empty for an open slot.
    recipientId: str
    label: str
    # A role's owner and generation now; null for other slots.
    ownerId: str | None
    ownershipGeneration: int | None
    acknowledged: bool
    # The first to acknowledge it.
    acknowledgedBy: str | None
    # How far the slot got (v33): pending, seen (a command of the target's listed or read it),
    # acknowledged, answered (the target posted in the thread after the request). The defaults read a
    # retry receipt stored before v33.
    delivery: Literal["pending", "seen", "acknowledged", "answered"] = "pending"
    seenAt: str | None = None
    acknowledgedAt: str | None = None
    answeredAt: str | None = None
    answerMessageId: str | None = None
    # An agent slot's agent, or a role's owner: whether it can be reached; null for other slots.
    presence: Presence | None = None


class AcknowledgementReceipt(Answer):
    id: str
    targetId: str
    actorId: str
    actorLabel: str
    actorKind: Literal["agent", "human"]
    # The role it acknowledged for, and that role's generation then.
    roleId: str | None
    ownershipGeneration: int | None
    createdAt: str
    # Given after the request ended, or for a slot already acknowledged.
    late: bool


class AcknowledgementRequest(Answer):
    """A message's request to be acknowledged, as the caller sees it."""

    # The message's id.
    id: str
    mode: Literal["all", "any", "any_agent", "anyone"]
    state: Literal["pending", "satisfied", "released_by_human", "cancelled"]
    targets: list[AcknowledgementTarget]
    receipts: list[AcknowledgementReceipt]
    # The slots the caller may acknowledge now.
    actionableTargetIds: list[str]
    canCancel: bool
    canRelease: bool
    classifications: list[Literal["personal", "managed", "predecessor", "actionable", "unassigned"]]
    # Set when the request ends: why, when and by whom.
    reason: str | None
    terminalAt: str | None
    terminalActorId: str | None
    terminalActorLabel: str | None
    terminalActorKind: str | None
    # The role that cancelled it on its behalf, and that role's generation then.
    authorityRoleId: str | None
    authorityGeneration: int | None
    # When it escalates to the person if still unanswered, whether it was sent urgent, when it
    # escalated, and whether its deadline has passed while it is pending (v33). The defaults read a
    # retry receipt stored before v33.
    deadlineAt: str | None = None
    urgent: bool = False
    escalatedAt: str | None = None
    overdue: bool = False


class DiscussionMessage(Answer):
    id: str
    discussionId: str
    text: str
    authorId: str
    authorLabel: str
    authorKind: Literal["agent", "human"]
    createdAt: str
    # The chat its author reported; null when it named none.
    chatId: str | None
    # The role it was sent on behalf of, and that role's generation then.
    onBehalfOfRoleId: str | None
    ownershipGeneration: int | None
    # Null unless it asks to be acknowledged.
    request: AcknowledgementRequest | None


class ThreadNamed(Answer):
    id: str
    title: str


class RequestListed(Answer):
    """A message with an acknowledgement request, with the thread it is in."""

    discussion: ThreadNamed
    message: DiscussionMessage


class DiscussionRequests(Answer):
    """Requests the caller sent or that have a slot for it, newest first (v33)."""

    items: list[RequestListed]
    total: int
    limit: int
    offset: int


class InboxChange(Answer):
    """An acknowledgement, cancel, release or escalation of a request the caller manages."""

    seq: int
    kind: str
    discussionId: str | None
    messageId: str | None
    roleId: str | None
    actorId: str
    actorLabel: str
    actorKind: str
    createdAt: str


class DiscussionInbox(Answer):
    """What waits on the caller and what came back to it (v33)."""

    # Pending requests with a slot the caller may acknowledge now, newest first, at most 50.
    received: list[RequestListed]
    # How many of the requests waiting on the caller it had not seen before this read.
    unseen: int
    # Messages others posted in the thread of a request the caller manages, after that request.
    answers: list[RequestListed]
    changes: list[InboxChange]
    # The discussions feed's cursor at this read: wait from it with `since`.
    cursor: str


class DiscussionThread(Answer):
    """A discussion with a page of its messages, oldest first."""

    discussion: DiscussionSummary
    messages: list[DiscussionMessage]
    total: int
    limit: int
    offset: int


class DiscussionChange(Answer):
    """One change in the discussions feed, after the cursor it was read from."""

    seq: int
    kind: str
    discussionId: str | None
    messageId: str | None
    roleId: str | None
    actorId: str
    actorLabel: str
    # events.actor_kind has no CHECK.
    actorKind: str
    createdAt: str


class DiscussionChanges(Answer):
    items: list[DiscussionChange]
    # Where to read on from; opaque.
    cursor: str
    hasMore: bool


class ResumeCounts(Answer):
    roles: int
    discussions: int
    requests: int


class DiscussionResume(Answer):
    """What an agent needs to pick up a work context's discussions: roles, threads, pending requests and a feed cursor."""

    roles: list[DiscussionRole]
    discussions: list[DiscussionSummary]
    # Messages whose requests are pending, oldest first.
    requests: list[DiscussionMessage]
    cursor: str
    counts: ResumeCounts
    limit: int
    offset: int
    hasMore: bool


class ChatOwner(Answer):
    """The work item or roadmap a tracked chat belongs to; empty if that record is missing."""

    type: Literal["item", "roadmap"]
    id: str
    title: str
    archived: bool


class ChatStep(Answer):
    """A step a tracked chat works on, with its status as its roadmap shows it."""

    id: str
    title: str
    # steps.status has no CHECK.
    status: str
    retired: bool


class ChatAgent(Answer):
    """An agent identity tied to a chat ID, by its closest tie."""

    id: str
    label: str
    avatar: str | None
    active: bool
    # Registered with the chat, or reported by an open run.
    binding: Literal["registered", "run"]
    # The chat ID as that tie stored it.
    chatId: str


class TranscriptThread(Answer):
    """One agent's transcript in a chat: the chat itself or a subagent it started."""

    key: str
    name: str
    title: str
    task: str
    # The thread that started it; null for the chat itself.
    parent: str | None
    model: str
    state: Literal["working", "idle"]
    lastActivityAt: str | None


class TranscriptSummary(Answer):
    """A chat's agents and their latest activity, read from the tool's transcript files on this machine."""

    # Whether any transcript of the chat was found.
    found: bool
    # Which tool wrote it; null when none was found.
    source: Literal["claude", "codex"] | None
    lastActivityAt: str | None
    state: Literal["working", "idle"]
    threads: list[TranscriptThread]


class UnreadTranscript(TranscriptSummary):
    """A chat's summary when its transcript could not be read: it never fails the list."""

    error: str


class ChatListed(TrackedChat):
    """A tracked chat as the Chats page lists it."""

    owner: ChatOwner
    # The roadmaps its record sits within, from the top level down.
    roadmaps: list[RecordSummary]
    steps: list[ChatStep]
    # The agent identities tied to its own chat ID, compared exactly, as runs report it.
    agents: list[ChatAgent]
    # The runs reported on its chat ID that have not ended, the latest first.
    openRuns: list[AgentRun]
    transcript: TranscriptSummary | UnreadTranscript


class Chats(Answer):
    items: list[ChatListed]


class SessionChat(Answer):
    """A tracked chat that follows a running session."""

    id: str
    title: str
    archived: bool
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    # Empty if that record is missing.
    ownerTitle: str


class ClaudeSession(Answer):
    """A Claude Code session running on this machine, with the chats and agents tied to it."""

    sessionId: str
    # The desktop app's own session ID, when the app runs it.
    hostSessionId: str | None
    name: str
    cwd: str
    entrypoint: str
    kind: str
    status: Literal["waiting", "working", "idle"]
    # What it waits on, while it waits for the person.
    waitingFor: str
    startedAt: str | None
    statusAt: str | None
    version: str
    # The chat ID to track it by: the closest tie's, else one a record tracks it under, else its own.
    chatId: str
    agents: list[ChatAgent]
    tracked: list[SessionChat]
    # Its latest model, from its transcript; empty when unknown.
    model: str


class ClaudeSessions(Answer):
    # Waiting on the person first, then working, then idle, each the most recently changed first.
    items: list[ClaudeSession]


class ToolCall(Answer):
    name: str
    summary: str


class TranscriptText(Answer):
    """A message of a chat's transcript, its text cut at the transcript's limit."""

    id: str
    # The thread that recorded it.
    thread: str
    at: str | None
    text: str
    truncated: bool
    author: str


class UserMessage(TranscriptText):
    kind: Literal["user"]


class AssistantMessage(TranscriptText):
    kind: Literal["assistant"]
    authorThread: str


class AgentMessage(TranscriptText):
    """A message one agent of the chat sent another, or one received from outside it."""

    kind: Literal["agent"]
    # The sender's thread, when the sender is an agent of this chat.
    authorThread: str | None
    recipient: str
    # A Codex envelope's message type, and whether its content is encrypted, when it has them:
    # the answer leaves out either one it doesn't have.
    messageType: str | None = None
    encrypted: bool | None = None


class ToolsMessage(Answer):
    """A run of tool calls between messages, as one line of activity."""

    id: str
    thread: str
    at: str | None
    kind: Literal["tools"]
    count: int
    # The first few.
    tools: list[ToolCall]
    until: str | None
    author: str
    authorThread: str


class Transcript(TranscriptSummary):
    """A tracked chat's latest transcript messages, oldest first, with its agents."""

    chat: TrackedChat
    messages: list[UserMessage | AssistantMessage | AgentMessage | ToolsMessage]
    # Kept messages and those let go, of the threads read.
    total: int
    more: bool




class SearchResult(Answer):
    id: str
    type: Literal["item", "step", "roadmap", "note", "attachment", "link", "chat", "github", "question", "decision",
                  "person", "ticket", "ticket_comment", "discussion", "discussion_message", "resource"]
    title: str
    snippet: str
    ownerId: str
    # The page a result opens on, and the record that page shows; null if that record is missing.
    routeType: Literal["resources", "discussions", "tickets", "work", "people", "repositories", "roadmaps"]
    routeId: str | None


class SearchResults(Answer):
    items: list[SearchResult]
    total: int


class WorkContext(Answer):
    """What an agent reads before it starts: the day, the work, the plans, the people, the proposals,
    who is at work where, and the tickets and resources."""

    day: Day
    workItems: WorkItems
    # Without the people tied to each, which the list of roadmaps adds.
    roadmaps: PlanRoadmaps
    people: People
    # The open ones only, as summaries.
    proposals: OpenProposals
    repositories: Repositories
    runs: AgentRuns
    participations: Participations
    updates: CoordinationUpdates
    tickets: Tickets
    resources: Resources


class ContextWorkItem(Answer):
    id: str
    title: str
    status: str
    # The person's stack rank, 1 first; null when the item is done or archived.
    rank: int | None


class ContextWorkItems(Answer):
    items: list[ContextWorkItem]
    total: int


class ContextRoadmap(Answer):
    id: str
    title: str
    # Its questions still open, archived ones aside.
    openQuestions: int


class ContextRoadmaps(Answer):
    items: list[ContextRoadmap]
    total: int


class ContextPerson(Answer):
    id: str
    name: str


class ContextPeople(Answer):
    items: list[ContextPerson]
    total: int


class ContextUpdate(Answer):
    """A status update as the summary lists it: its first line, cut to CONTEXT_LINE characters."""

    id: str
    kind: str
    text: str
    authorLabel: str
    participationId: str
    createdAt: str


class ContextUpdates(Answer):
    # The latest ten; total counts every one the filters match.
    items: list[ContextUpdate]
    total: int


class ContextSummary(Answer):
    """What an agent reads first, by default (audit finding P3): titles, IDs and states. The work,
    plans and people by name; the latest ten updates, one line each; the day, who is at work where,
    the open proposals, tickets and resources as the whole context gives them. detail=full answers
    the whole context, and each record's own read its text."""

    day: Day
    workItems: ContextWorkItems
    roadmaps: ContextRoadmaps
    people: ContextPeople
    proposals: OpenProposals
    repositories: Repositories
    runs: AgentRuns
    participations: Participations
    updates: ContextUpdates
    tickets: Tickets
    resources: Resources


class Done(Answer):
    """A command that changed what it was asked to, or found nothing to change, and names nothing back."""

    ok: Literal[True]


class RankResult(Answer):
    """A work item's stack rank after a move, 1 first, among how many ranked items."""

    id: str
    rank: int
    rankedCount: int


class SavedRoadmap(PlanRoadmap):
    """A roadmap as its commands return it: its steps, retired ones included, and versions. The
    supporting lists are always empty here; its read fills them."""

    steps: list[PlanStep]
    versions: list[RoadmapVersion]
    notes: list[Note]
    attachments: list[Attachment]
    links: list[WebLink]
    chats: list[TrackedChat]
    github: list[ListedGithubReference]
    questions: list[Question]
    decisions: list[Decision]


class SavedStep(PlanStep):
    """A step as its update returns it, with its roadmap."""

    roadmapId: str


class CreatedStep(SavedStep):
    """A step as step.create or step.move returns it, with its updated roadmap revision."""

    roadmapRevision: int


class SplitStepResult(Answer):
    """A split leaf step and its newly created single-repo child steps."""

    parent: PlanStep
    children: list[PlanStep]
    roadmapId: str
    roadmapRevision: int


class WaveSummary(Answer):
    id: str
    roadmapId: str
    name: str
    summary: str
    status: Literal["planning", "active", "completed", "cancelled"]
    position: int
    discussionId: str | None
    archived: bool
    revision: int
    createdAt: str
    updatedAt: str
    activatedAt: str | None
    completedAt: str | None
    stepCount: int
    doneStepCount: int
    frozenRepoCount: int
    pendingRepinCount: int


class Waves(Answer):
    items: list[WaveSummary]
    total: int


class WaveStep(Answer):
    id: str
    title: str
    status: str
    workClass: str
    parentId: str | None
    repositoryIds: list[str]


class WaveRepository(Answer):
    repositoryId: str
    repositoryName: str
    tier: int | None
    freezeStatus: Literal["open", "batching", "frozen"]
    frozenRef: str
    frozenAt: str | None
    frozenByLabel: str
    notes: str
    revision: int
    updatedAt: str


class WaveRepin(Answer):
    waveId: str
    upstreamRepoId: str
    upstreamRepoName: str
    consumerRepoId: str
    consumerRepoName: str
    status: Literal["pending", "in_progress", "verified", "waived"]
    pinnedRef: str
    prUrl: str
    commitSha: str
    reportedByLabel: str
    verifiedAt: str | None
    revision: int
    updatedAt: str


class WaveTierRepo(WaveRepository):
    stepCount: int
    doingCount: int
    doneCount: int
    blockedCount: int
    pendingDownstreamRepins: int
    pendingUpstreamRepins: int


class WaveTierGroup(Answer):
    tier: int | None
    repositories: list[WaveTierRepo]


class WaveDetail(WaveSummary):
    stepIds: list[str]
    steps: list[WaveStep]
    repositories: list[WaveRepository]
    repins: list[WaveRepin]
    tierPipeline: list[WaveTierGroup]


class DispatchRoadmapRef(Answer):
    id: str
    title: str
    revision: int
    archived: bool


class DispatchSummary(Answer):
    totalLeaves: int
    doingLeaves: int
    blockedLeaves: int
    doneLeaves: int
    overCapRepos: int
    competingDoingCount: int
    multiRepoLeafCount: int
    unassignedLeafCount: int
    pendingRepinCount: int


class DispatchPhaseLink(Answer):
    id: str
    title: str


class DispatchStepAgent(Answer):
    agentId: str
    label: str
    avatar: str | None
    stale: bool


class DispatchStepCard(Answer):
    id: str
    title: str
    status: str
    revision: int
    workClass: str
    repositoryIds: list[str]
    multiRepo: bool
    waveId: str | None
    inActiveWave: bool
    queuePosition: int | None
    phasePath: list[DispatchPhaseLink]
    activeAgents: list[DispatchStepAgent]


class DispatchUnassignedStepCard(DispatchStepCard):
    suggestedRepoIds: list[str]


class DispatchParticipation(Answer):
    participationId: str
    agentId: str
    agentLabel: str
    avatar: str | None
    runName: str
    itemId: str | None
    stepId: str | None
    description: str
    branch: str
    worktreePath: str
    lastSeenAt: str
    stale: bool


class DispatchRepoLane(Answer):
    repositoryId: str
    repositoryName: str
    url: str
    discussionId: str | None
    wipCap: int | None
    effectiveWipCap: int | None
    tier: int | None
    upstreamIds: list[str]
    downstreamIds: list[str]
    slotSummary: RepositorySlotSummary
    activeWriters: int
    doingCount: int
    overWipCap: bool
    freezeState: WaveRepository | None
    repins: list[WaveRepin]
    doing: list[DispatchStepCard]
    competingWip: list[DispatchStepCard]
    queued: list[DispatchStepCard]
    blocked: list[DispatchStepCard]
    done: list[DispatchStepCard]
    blockedCount: int
    doneCount: int
    participations: list[DispatchParticipation]


class DispatchPhaseRollup(Answer):
    id: str
    title: str
    status: str
    totalLeaves: int
    doneLeaves: int
    doingLeaves: int
    blockedLeaves: int
    openLeaves: int


class DispatchGates(Answer):
    gateSteps: list[DispatchStepCard]
    phases: list[DispatchPhaseRollup]


class RoadmapDispatch(Answer):
    roadmap: DispatchRoadmapRef
    summary: DispatchSummary
    waves: list[WaveSummary]
    activeWave: WaveDetail | None
    repoLanes: list[DispatchRepoLane]
    multiRepoLeaves: list[DispatchStepCard]
    unassignedLeaves: list[DispatchUnassignedStepCard]
    gates: DispatchGates


class RepositoryQueueFreeze(Answer):
    waveId: str
    waveName: str
    roadmapId: str
    roadmapTitle: str
    tier: int | None
    freezeStatus: Literal["open", "batching", "frozen"]
    frozenRef: str
    frozenAt: str | None
    notes: str


class RepositoryQueueRepin(Answer):
    waveId: str
    waveName: str
    upstreamRepoId: str
    upstreamRepoName: str
    consumerRepoId: str
    consumerRepoName: str
    status: Literal["pending", "in_progress", "verified", "waived"]
    pinnedRef: str
    prUrl: str
    commitSha: str
    reportedByLabel: str
    verifiedAt: str | None
    revision: int
    updatedAt: str


class RepositoryQueueStep(Answer):
    id: str
    roadmapId: str
    roadmapTitle: str
    title: str
    status: str
    revision: int
    workClass: str
    queuePosition: int | None
    waveId: str | None
    waveName: str | None
    waveStatus: str | None
    multiRepo: bool


class RepositoryQueue(Answer):
    repositoryId: str
    repositoryName: str
    wipCap: int | None
    effectiveWipCap: int | None
    tier: int | None
    upstreamIds: list[str]
    downstreamIds: list[str]
    activeWriters: int
    doingCount: int
    overWipCap: bool
    freezes: list[RepositoryQueueFreeze]
    repins: list[RepositoryQueueRepin]
    participations: list[DispatchParticipation]
    doing: list[RepositoryQueueStep]
    queued: list[RepositoryQueueStep]
    blocked: list[RepositoryQueueStep]
    done: list[RepositoryQueueStep]
    doneCount: int


class PersonTie(RecordPerson):
    """A person's tie to a work item, roadmap or repository, as setting it returns it."""

    targetType: Literal["item", "roadmap", "repository"]
    targetId: str


class PersonUntied(Answer):
    """The tie a person no longer has."""

    ok: Literal[True]
    id: str
    personId: str
    targetType: Literal["item", "roadmap", "repository"]
    targetId: str


class EnsuredRepository(DiscussedRepository):
    """A repository as registering its address returns it: the record that holds it, new or not."""

    # False when a record already held the address.
    created: bool


class ChatTracked(TrackedChat):
    """An agent's own chat as tracking it returns it: the record that tracks it, new or not."""

    # False when a record already tracked the chat there.
    created: bool


class Registration(Answer):
    """A credential an agent registered for itself, and the identity it holds."""

    id: str
    agentId: str
    label: str
    avatar: str | None
    # The chat its registration named; null when it named none.
    chatId: str | None
    # The agent that registered it; null for one issued in Settings.
    registeredBy: CredentialSponsor | None
    # The directory it registered from; null when it named none.
    directory: str | None


class ContinuedStep(Answer):
    id: str
    title: str


class ContinuedChat(Answer):
    """Where the new chat is tracked now, with the steps it holds there."""

    id: str
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    ownerTitle: str
    title: str
    steps: list[ContinuedStep]
    # False where the new chat was tracked already.
    created: bool


class ContinuedRole(Answer):
    """A responsibility role the identity owns, on the record its context names."""

    id: str
    label: str
    contextType: Literal["item", "roadmap", "step"]
    contextId: str
    # Null for a record that no longer resolves.
    contextTitle: str | None


class Continued(Answer):
    """What continuing in the new chat changed, and the roles that stay the identity's."""

    # The credential that asked; it and any other live credential of the identity are revoked.
    credentialId: str
    # The chat the identity worked in before; null when it registered before chats were recorded.
    chatId: str | None
    runsEnded: int
    chats: list[ContinuedChat]
    roles: list[ContinuedRole]


class Continuation(Registration):
    """The new credential of an identity that continues in another chat."""

    continued: Continued


class Retirement(Answer):
    """An agent's own credential, retired, with how many credentials it had registered went with it."""

    id: str
    agentId: str
    retired: Literal[True]
    registeredRevoked: int


class DiscussionStarted(Answer):
    """A new discussion and its first message."""

    discussion: DiscussionSummary
    message: DiscussionMessage


class CredentialIssued(Answer):
    """A credential as issuing it returns it. Its token shows only here; Worklog keeps its hash."""

    id: str
    agentId: str
    label: str
    avatar: str | None
    # Null when the answer replays an earlier request with the same Idempotency-Key: the receipt
    # keeps no token.
    token: str | None


class CredentialRotated(Answer):
    """The credential that replaces a rotated one, for the same agent identity, with its token."""

    id: str
    agentId: str
    label: str
    # Null when the answer replays an earlier request with the same Idempotency-Key.
    token: str | None


class CredentialRevoked(Answer):
    ok: Literal[True]
    # The credentials it had registered, revoked with it.
    registeredRevoked: int


class HashedBackup(BackupFile):
    """A backup a person made, with its file's SHA-256."""

    sha256: str


class UsageWindow(Answer):
    """The days a usage report covers, both ends included, in Worklog's timezone."""

    days: int
    # Answered as `from`, which Python keeps for itself.
    from_: str = Field(alias="from")
    to: str


class ActorUses(Answer):
    """Uses by the person and by agents."""

    person: int
    agent: int


class UsageDay(Answer):
    """One day's uses: the pages the person opened, the reads (GET) and changes (any other method) each
    made, and the calls that failed."""

    day: str
    pageViews: int
    reads: ActorUses
    changes: ActorUses
    failed: int


class PageUsed(Answer):
    page: str
    views: int
    lastUsed: str


class PageUnused(Answer):
    page: str
    # Null when nothing ever opened it.
    lastUsed: str | None


class CallUsed(Answer):
    # The method and route template, such as `POST /work-items/{identity}/archive`.
    call: str
    # Null for a call the running version no longer serves.
    scope: str | None
    person: int
    agent: int
    failed: int
    lastUsed: str


class CallUnused(Answer):
    call: str
    scope: str
    # Null when nothing ever made the call.
    lastUsed: str | None


class UsedPages(Answer):
    used: list[PageUsed]
    unused: list[PageUnused]


class UsedCalls(Answer):
    used: list[CallUsed]
    unused: list[CallUnused]


class UsageReport(Answer):
    """Which pages and API calls were used in a window and which nothing used (usage.summary)."""

    file: str
    version: str | None
    recordedSince: str | None
    window: UsageWindow
    # Whose uses the counts are (GET /usage?actor=): the person's or agents' alone, or null for everyone's.
    actor: Literal["person", "agent"] | None
    daily: list[UsageDay]
    pages: UsedPages
    calls: UsedCalls


class LiveRoadmapOption(Answer):
    id: str
    title: str
    parentId: str | None
    openQuestions: int


class LiveSummary(Answer):
    activeAgents: int
    workingChats: int
    activeWorkItems: int
    activeSteps: int
    activeRepos: int
    repoOverlaps: int
    overCapRepos: int
    pendingRequests: int
    waitingOnAgents: int
    waitingProposals: int
    openQuestions: int
    needsYou: int


class LiveWorkNode(Answer):
    id: str
    kind: Literal["roadmap", "step", "item"]
    title: str
    status: Literal["open", "doing", "waiting", "blocked", "done"]
    roadmapId: str | None
    roadmapTitle: str | None
    parentId: str | None
    stepTitle: str | None
    rank: int | None
    openQuestions: int
    waitingProposals: int
    waitingProposalIds: list[str]
    agentCount: int
    needsYou: bool
    href: str


class LiveAgentChat(Answer):
    id: str
    chatId: str
    title: str
    provider: str
    model: str
    working: bool
    lastAt: str | None
    href: str


class LiveAgentNode(Answer):
    id: str
    label: str
    avatar: str | None
    active: bool
    state: Literal["working", "waiting", "reporting", "stale", "idle", "revoked"]
    declaredState: Literal["reporting", "stale", "none", "revoked"]
    sessionState: Literal["working", "waiting", "idle"] | None
    providers: list[str]
    model: str
    registeredById: str | None
    registeredByLabel: str | None
    openRuns: int
    openParticipations: int
    roles: list[str]
    chats: list[LiveAgentChat]
    lastAt: str | None
    latestNote: str
    href: str


class LiveUntrackedSession(Answer):
    sessionId: str
    hostSessionId: str | None
    name: str
    state: Literal["working", "waiting", "idle"]
    entrypoint: str
    cwd: str
    model: str
    startedAt: str


class LiveRepoNode(Answer):
    id: str
    name: str
    url: str | None
    unreviewed: bool
    discussionId: str | None
    branches: list[str]
    agentCount: int
    overlapCount: int
    wipCap: int | None
    effectiveWipCap: int | None
    tier: int | None
    doingStepCount: int
    overWipCap: bool
    frozenWaveName: str | None
    pendingRepinsCount: int
    maxActiveLeases: int
    activeLeases: int
    queuedLeases: int
    dirtySlots: int
    rescuedLeases: int
    baseReadonly: bool
    lastAt: str | None
    href: str


class LiveWorkEdge(Answer):
    id: str
    agentId: str
    workId: str
    workKind: Literal["roadmap", "step", "item"]
    via: list[Literal["participation", "role", "chat", "proposal", "discussion"]]
    active: bool
    stale: bool
    lastAt: str | None


class LiveRepoEdge(Answer):
    id: str
    agentId: str
    repositoryId: str
    via: list[Literal["participation", "chat", "update"]]
    branch: str
    worktreePath: str
    active: bool
    stale: bool
    lastAt: str | None


class LiveCommEdge(Answer):
    id: str
    fromKind: Literal["agent", "person"]
    fromId: str
    fromLabel: str
    toKind: Literal["agent", "person"]
    toId: str
    toLabel: str
    kind: Literal["ack_request", "role_offer", "shared_thread", "sponsorship"]
    status: Literal["pending", "satisfied", "active"]
    label: str
    discussionId: str | None
    messageId: str | None
    lastAt: str | None
    href: str


class LiveSwimlaneSpan(Answer):
    id: str
    agentId: str
    label: str
    workId: str | None
    workKind: Literal["roadmap", "step", "item"] | None
    repositoryId: str | None
    repositoryName: str | None
    branch: str
    startedAt: str
    endedAt: str | None
    lastSeenAt: str
    stale: bool
    href: str


class LivePulse(Answer):
    id: str
    kind: Literal[
        "update",
        "blocker",
        "message",
        "ack_requested",
        "ack_satisfied",
        "role_handoff",
        "proposal_submitted",
        "proposal_approved",
        "participation_started",
        "run_started",
    ]
    at: str
    actorKind: Literal["agent", "person"]
    actorId: str
    actorLabel: str
    targetActorId: str | None
    targetActorLabel: str | None
    workId: str | None
    workTitle: str | None
    repositoryId: str | None
    repositoryName: str | None
    discussionId: str | None
    proposalId: str | None
    summary: str
    href: str


class LiveSnapshot(Answer):
    """Everything in motion now across plans, agents, chats, discussions and repositories."""

    generatedAt: str
    windowMinutes: int
    roadmapId: str | None
    roadmapTitle: str | None
    summary: LiveSummary
    roadmaps: list[LiveRoadmapOption]
    work: list[LiveWorkNode]
    agents: list[LiveAgentNode]
    untrackedSessions: list[LiveUntrackedSession]
    repositories: list[LiveRepoNode]
    workEdges: list[LiveWorkEdge]
    repoEdges: list[LiveRepoEdge]
    commEdges: list[LiveCommEdge]
    lanes: list[LiveSwimlaneSpan]
    pulses: list[LivePulse]
