import { request } from "./api";
import type { Answer, Schema } from "./apiTypes";
/** A command archives a record, or recovers it. */
const verb = (archived: boolean) => (archived ? "archive" : "recover");
/** The records whose routes archive and recover them as /{kind}/{id}/archive and /recover. */
export type ArchiveKind =
  | "notes"
  | "tasks"
  | "references"
  | "attachments"
  | "links"
  | "chats"
  | "github"
  | "questions"
  | "decisions"
  | "release-checks";
/** What a proposal's author or the person can do to it next. */
export type ProposalVerb =
  "submit" | "withdraw" | "reopen" | "approve" | "reject";
/**
 * Every write a page makes, each with the body the backend's schema takes and the answer it
 * gives. A page calls these, inside useCommand or useWrites, rather than a path of its own.
 */
export const commands = {
  createItem: (body: Schema["ItemCreate"]) =>
    request<Answer<"/api/v1/work-items", "post">>("/work-items", "POST", body),
  updateItem: (id: string, body: Schema["ItemUpdate"]) =>
    request<Answer<"/api/v1/work-items/{identity}", "patch">>(
      "/work-items/" + id,
      "PATCH",
      body,
    ),
  rankItem: (id: string, body: Schema["Rank"]) =>
    request<Answer<"/api/v1/work-items/{identity}/rank", "post">>(
      `/work-items/${id}/rank`,
      "POST",
      body,
    ),
  upsertEntry: (date: string, id: string, body: Schema["EntryUpdate"]) =>
    request<Answer<"/api/v1/days/{date}/entries/{item_id}", "put">>(
      `/days/${date}/entries/${id}`,
      "PUT",
      body,
    ),
  createNote: (body: Schema["NoteCreate"]) =>
    request<Answer<"/api/v1/notes", "post">>("/notes", "POST", body),
  updateNote: (id: string, body: Schema["NoteUpdate"]) =>
    request<Answer<"/api/v1/notes/{identity}", "patch">>(
      "/notes/" + id,
      "PATCH",
      body,
    ),
  createTask: (body: Schema["TaskCreate"]) =>
    request<Answer<"/api/v1/tasks", "post">>("/tasks", "POST", body),
  updateTask: (id: string, body: Schema["TaskUpdate"]) =>
    request<Answer<"/api/v1/tasks/{identity}", "patch">>(
      "/tasks/" + id,
      "PATCH",
      body,
    ),
  createRoadmap: (body: Schema["RoadmapCreate"]) =>
    request<Answer<"/api/v1/roadmaps", "post">>("/roadmaps", "POST", body),
  reviseRoadmap: (id: string, body: Schema["RoadmapRevision"]) =>
    request<Answer<"/api/v1/roadmaps/{identity}/revisions", "post">>(
      `/roadmaps/${id}/revisions`,
      "POST",
      body,
    ),
  /** Fields left out start empty. */
  createPerson: (
    body: Pick<Schema["PersonCreate"], "name"> &
      Partial<Schema["PersonCreate"]>,
  ) => request<Answer<"/api/v1/people", "post">>("/people", "POST", body),
  updatePerson: (id: string, body: Schema["PersonUpdate"]) =>
    request<Answer<"/api/v1/people/{identity}", "patch">>(
      "/people/" + id,
      "PATCH",
      body,
    ),
  linkPerson: (body: Schema["PersonLinkCreate"]) =>
    request<Answer<"/api/v1/person-links", "post">>(
      "/person-links",
      "POST",
      body,
    ),
  unlinkPerson: (body: Schema["PersonLinkRemove"]) =>
    request<Answer<"/api/v1/person-links/unlink", "post">>(
      "/person-links/unlink",
      "POST",
      body,
    ),
  createLink: (body: Schema["LinkCreate"]) =>
    request<Answer<"/api/v1/links", "post">>("/links", "POST", body),
  updateLink: (id: string, body: Schema["LinkUpdate"]) =>
    request<Answer<"/api/v1/links/{identity}", "patch">>(
      "/links/" + id,
      "PATCH",
      body,
    ),
  createChat: (body: Schema["ChatCreate"]) =>
    request<Answer<"/api/v1/chats", "post">>("/chats", "POST", body),
  updateChat: (id: string, body: Schema["ChatUpdate"]) =>
    request<Answer<"/api/v1/chats/{identity}", "patch">>(
      "/chats/" + id,
      "PATCH",
      body,
    ),
  createGithubRef: (body: Schema["GithubRefCreate"]) =>
    request<Answer<"/api/v1/github", "post">>("/github", "POST", body),
  updateGithubRef: (id: string, body: Schema["GithubRefUpdate"]) =>
    request<Answer<"/api/v1/github/{identity}", "patch">>(
      "/github/" + id,
      "PATCH",
      body,
    ),
  createTicket: (body: Schema["TicketCreate"]) =>
    request<Answer<"/api/v1/tickets", "post">>("/tickets", "POST", body),
  updateTicket: (id: string, body: Schema["TicketUpdate"]) =>
    request<Answer<"/api/v1/tickets/{ref}", "patch">>(
      "/tickets/" + id,
      "PATCH",
      body,
    ),
  commentOnTicket: (id: string, body: Schema["TicketCommentCreate"]) =>
    request<Answer<"/api/v1/tickets/{ref}/comments", "post">>(
      `/tickets/${id}/comments`,
      "POST",
      body,
    ),
  setTicketArchived: (
    id: string,
    archived: boolean,
    body: Schema["Expected"],
  ) =>
    request<Answer<"/api/v1/tickets/{ref}/{verb}", "post">>(
      `/tickets/${id}/${verb(archived)}`,
      "POST",
      body,
    ),
  createResource: (body: Schema["ResourceCreate"]) =>
    request<Answer<"/api/v1/resources", "post">>("/resources", "POST", body),
  updateResource: (id: string, body: Schema["ResourceUpdate"]) =>
    request<Answer<"/api/v1/resources/{identity}", "patch">>(
      "/resources/" + id,
      "PATCH",
      body,
    ),
  setResourceArchived: (
    id: string,
    archived: boolean,
    body: Schema["Expected"],
  ) =>
    request<Answer<"/api/v1/resources/{identity}/{verb}", "post">>(
      `/resources/${id}/${verb(archived)}`,
      "POST",
      body,
    ),
  /** Fields left out take their defaults: no date, planned. */
  createRelease: (
    body: Pick<Schema["ReleaseCreate"], "title"> &
      Partial<Schema["ReleaseCreate"]>,
  ) => request<Answer<"/api/v1/releases", "post">>("/releases", "POST", body),
  updateRelease: (id: string, body: Schema["ReleaseUpdate"]) =>
    request<Answer<"/api/v1/releases/{identity}", "patch">>(
      "/releases/" + id,
      "PATCH",
      body,
    ),
  setReleaseArchived: (
    id: string,
    archived: boolean,
    body: Schema["Expected"],
  ) =>
    request<Answer<"/api/v1/releases/{identity}/archive", "post">>(
      `/releases/${id}/${verb(archived)}`,
      "POST",
      body,
    ),
  /** Holds a work item, roadmap, step or GitHub reference; holding one twice changes nothing. */
  holdInRelease: (body: Schema["ReleaseContentChange"]) =>
    request<Answer<"/api/v1/release-contents", "post">>(
      "/release-contents",
      "POST",
      body,
    ),
  removeFromRelease: (body: Schema["ReleaseContentChange"]) =>
    request<Answer<"/api/v1/release-contents/remove", "post">>(
      "/release-contents/remove",
      "POST",
      body,
    ),
  createReleaseCheck: (
    body: Pick<Schema["ReleaseCheckCreate"], "releaseId" | "kind" | "text"> &
      Partial<Schema["ReleaseCheckCreate"]>,
  ) =>
    request<Answer<"/api/v1/release-checks", "post">>(
      "/release-checks",
      "POST",
      body,
    ),
  updateReleaseCheck: (id: string, body: Schema["ReleaseCheckUpdate"]) =>
    request<Answer<"/api/v1/release-checks/{identity}", "patch">>(
      "/release-checks/" + id,
      "PATCH",
      body,
    ),
  setReleaseCheckArchived: (
    id: string,
    archived: boolean,
    body: Schema["Expected"],
  ) =>
    request<Answer<"/api/v1/release-checks/{identity}/archive", "post">>(
      `/release-checks/${id}/${verb(archived)}`,
      "POST",
      body,
    ),
  createReminder: (body: Schema["ReminderCreate"]) =>
    request<Answer<"/api/v1/reminders", "post">>("/reminders", "POST", body),
  updateReminder: (id: string, body: Schema["ReminderUpdate"]) =>
    request<Answer<"/api/v1/reminders/{identity}", "patch">>(
      "/reminders/" + id,
      "PATCH",
      body,
    ),
  setReminderArchived: (
    id: string,
    archived: boolean,
    body: Schema["Expected"],
  ) =>
    request<Answer<"/api/v1/reminders/{identity}/{verb}", "post">>(
      `/reminders/${id}/${verb(archived)}`,
      "POST",
      body,
    ),

  // Work items and days.
  setItemArchived: (id: string, archived: boolean, body: Schema["Expected"]) =>
    request<Answer<"/api/v1/work-items/{identity}/{verb}", "post">>(
      `/work-items/${id}/${verb(archived)}`,
      "POST",
      body,
    ),
  removeEntry: (date: string, itemId: string, body: Schema["Expected"]) =>
    request<Answer<"/api/v1/days/{date}/entries/{item_id}/remove", "post">>(
      `/days/${date}/entries/${itemId}/remove`,
      "POST",
      body,
    ),
  reorderDay: (date: string, body: Schema["Reorder"]) =>
    request<Answer<"/api/v1/days/{date}/reorder", "post">>(
      `/days/${date}/reorder`,
      "POST",
      body,
    ),
  saveReflection: (date: string, body: Schema["Reflection"]) =>
    request<Answer<"/api/v1/days/{date}/reflection", "put">>(
      `/days/${date}/reflection`,
      "PUT",
      body,
    ),
  rescheduleEntry: (date: string, body: Schema["Reschedule"]) =>
    request<Answer<"/api/v1/days/{date}/reschedule", "post">>(
      `/days/${date}/reschedule`,
      "POST",
      body,
    ),

  // Supporting records.
  setArchived: <K extends ArchiveKind>(
    kind: K,
    id: string,
    archived: boolean,
    body: Schema["Expected"],
  ) =>
    request<Answer<`/api/v1/${K}/{identity}/archive`, "post">>(
      `/${kind}/${id}/${verb(archived)}`,
      "POST",
      body,
    ),
  /** Moves a task to a place among its item's active tasks, 1 first. */
  moveTask: (id: string, body: Schema["TaskMove"]) =>
    request<Answer<"/api/v1/tasks/{identity}/move", "post">>(
      `/tasks/${id}/move`,
      "POST",
      body,
    ),
  createReference: (body: Schema["ReferenceCreate"]) =>
    request<Answer<"/api/v1/references", "post">>("/references", "POST", body),
  /** Keeps a copy of the file, named as it is, on the record. */
  uploadAttachment: (ownerType: string, ownerId: string, file: File) =>
    request<Answer<"/api/v1/attachments", "post">>(
      `/attachments?ownerType=${ownerType}&ownerId=${ownerId}&name=${encodeURIComponent(file.name)}`,
      "POST",
      file,
    ),
  createQuestion: (body: Schema["QuestionCreate"]) =>
    request<Answer<"/api/v1/questions", "post">>("/questions", "POST", body),
  updateQuestion: (id: string, body: Schema["QuestionUpdate"]) =>
    request<Answer<"/api/v1/questions/{identity}", "patch">>(
      "/questions/" + id,
      "PATCH",
      body,
    ),
  createDecision: (body: Schema["DecisionCreate"]) =>
    request<Answer<"/api/v1/decisions", "post">>("/decisions", "POST", body),
  updateDecision: (id: string, body: Schema["DecisionUpdate"]) =>
    request<Answer<"/api/v1/decisions/{identity}", "patch">>(
      "/decisions/" + id,
      "PATCH",
      body,
    ),

  // Roadmaps, steps and plan imports.
  setRoadmapArchived: (
    id: string,
    archived: boolean,
    body: Schema["Expected"],
  ) =>
    request<Answer<"/api/v1/roadmaps/{identity}/{verb}", "post">>(
      `/roadmaps/${id}/${verb(archived)}`,
      "POST",
      body,
    ),
  moveRoadmap: (id: string, body: Schema["RoadmapMove"]) =>
    request<Answer<"/api/v1/roadmaps/{identity}/move", "post">>(
      `/roadmaps/${id}/move`,
      "POST",
      body,
    ),
  /** Makes a saved version the roadmap's text again, as a new revision. */
  restoreRoadmap: (id: string, body: Schema["Restore"]) =>
    request<Answer<"/api/v1/roadmaps/{identity}/restore", "post">>(
      `/roadmaps/${id}/restore`,
      "POST",
      body,
    ),
  updateStep: (id: string, body: Schema["StepUpdate"]) =>
    request<Answer<"/api/v1/steps/{identity}", "patch">>(
      "/steps/" + id,
      "PATCH",
      body,
    ),
  setStepProgress: (id: string, body: Schema["Progress"]) =>
    request<Answer<"/api/v1/steps/{identity}/progress", "patch">>(
      `/steps/${id}/progress`,
      "PATCH",
      body,
    ),
  /** Reads a plan from an HTML file; nothing is saved until the preview is committed. */
  previewImport: (file: File) =>
    request<Answer<"/api/v1/imports/preview", "post">>(
      "/imports/preview?name=" + encodeURIComponent(file.name),
      "POST",
      file,
    ),
  commitImport: (id: string, body: Schema["ImportCommit"]) =>
    request<Answer<"/api/v1/imports/{identity}/commit", "post">>(
      `/imports/${id}/commit`,
      "POST",
      body,
    ),

  // People.
  setPersonArchived: (
    id: string,
    archived: boolean,
    body: Schema["Expected"],
  ) =>
    request<Answer<"/api/v1/people/{identity}/{verb}", "post">>(
      `/people/${id}/${verb(archived)}`,
      "POST",
      body,
    ),

  // Repositories.
  registerRepository: (body: Schema["RepositoryCreate"]) =>
    request<Answer<"/api/v1/repositories", "post">>(
      "/repositories",
      "POST",
      body,
    ),
  updateRepository: (id: string, body: Schema["RepositoryUpdate"]) =>
    request<Answer<"/api/v1/repositories/{identity}", "patch">>(
      "/repositories/" + id,
      "PATCH",
      body,
    ),
  /** Marks a repository an agent registered as one the person knows. */
  confirmRepository: (id: string, body: Schema["Expected"]) =>
    request<Answer<"/api/v1/repositories/{identity}/confirm", "post">>(
      `/repositories/${id}/confirm`,
      "POST",
      body,
    ),
  mergeRepository: (id: string, body: Schema["RepositoryMerge"]) =>
    request<Answer<"/api/v1/repositories/{identity}/merge", "post">>(
      `/repositories/${id}/merge`,
      "POST",
      body,
    ),
  setRepositoryArchived: (
    id: string,
    archived: boolean,
    body: Schema["Expected"],
  ) =>
    request<Answer<"/api/v1/repositories/{identity}/{verb}", "post">>(
      `/repositories/${id}/${verb(archived)}`,
      "POST",
      body,
    ),

  // Agents, their runs and ties, and chats.
  tieAgent: (body: Schema["AgentLinkSet"]) =>
    request<Answer<"/api/v1/agent-links", "post">>(
      "/agent-links",
      "POST",
      body,
    ),
  untieAgent: (body: Schema["AgentLinkRemove"]) =>
    request<Answer<"/api/v1/agent-links/unlink", "post">>(
      "/agent-links/unlink",
      "POST",
      body,
    ),
  endAgentRun: (id: string, body: Schema["Expected"]) =>
    request<Answer<"/api/v1/agent-runs/{identity}/end", "post">>(
      `/agent-runs/${id}/end`,
      "POST",
      body,
    ),
  sendChatMessage: (id: string, body: Schema["ChatMessageCreate"]) =>
    request<Answer<"/api/v1/chats/{identity}/messages", "post">>(
      `/chats/${id}/messages`,
      "POST",
      body,
    ),
  /** Confirms an agent as the one working in the chat, or takes that back. */
  setChatConfirmed: (
    id: string,
    confirmed: boolean,
    body: Schema["ChatConfirmation"],
  ) =>
    request<Answer<"/api/v1/chats/{identity}/confirmations", "post">>(
      `/chats/${id}/confirmations${confirmed ? "" : "/remove"}`,
      "POST",
      body,
    ),

  /**
   * Confirms an agent under a chat ID its run reports, as a running session's list does, or takes
   * that back; the chat need not be tracked.
   */
  setChatIdConfirmed: (
    confirmed: boolean,
    body: Schema["ChatIdConfirmation"],
  ) =>
    request<Answer<"/api/v1/chat-confirmations", "post">>(
      `/chat-confirmations${confirmed ? "" : "/remove"}`,
      "POST",
      body,
    ),

  // Discussions.
  startDiscussion: (body: Schema["DiscussionCreate"]) =>
    request<Answer<"/api/v1/discussions", "post">>(
      "/discussions",
      "POST",
      body,
    ),
  postMessage: (id: string, body: Schema["MessageCreate"]) =>
    request<Answer<"/api/v1/discussions/{identity}/messages", "post">>(
      `/discussions/${id}/messages`,
      "POST",
      body,
    ),
  /** Archives a thread, or recovers it, as the body's `archived` says. */
  setDiscussionArchived: (id: string, body: Schema["Archive"]) =>
    request<Answer<"/api/v1/discussions/{identity}/archive", "post">>(
      `/discussions/${id}/archive`,
      "POST",
      body,
    ),
  acknowledgeMessage: (id: string, body: Schema["Acknowledge"]) =>
    request<
      Answer<"/api/v1/discussions/messages/{identity}/acknowledge", "post">
    >(`/discussions/messages/${id}/acknowledge`, "POST", body),
  cancelRequest: (id: string, body: Schema["Empty"]) =>
    request<Answer<"/api/v1/discussions/messages/{identity}/cancel", "post">>(
      `/discussions/messages/${id}/cancel`,
      "POST",
      body,
    ),
  releaseRequest: (id: string, body: Schema["ReleaseRequest"]) =>
    request<Answer<"/api/v1/discussions/messages/{identity}/release", "post">>(
      `/discussions/messages/${id}/release`,
      "POST",
      body,
    ),
  createRole: (body: Schema["RoleCreate"]) =>
    request<Answer<"/api/v1/discussions/roles", "post">>(
      "/discussions/roles",
      "POST",
      body,
    ),
  reassignRole: (id: string, body: Schema["RoleReassign"]) =>
    request<Answer<"/api/v1/discussions/roles/{identity}/reassign", "post">>(
      `/discussions/roles/${id}/reassign`,
      "POST",
      body,
    ),

  // Proposals.
  transitionProposal: (
    id: string,
    to: ProposalVerb,
    body: Schema["Expected"],
  ) =>
    request<Answer<"/api/v1/proposals/{identity}/{verb}", "post">>(
      `/proposals/${id}/${to}`,
      "POST",
      body,
    ),

  /** Counts one visit to a page the sidebar or the Guide shows (usage.ts). */
  countPageView: (body: Schema["PageView"]) =>
    request<Answer<"/api/v1/page-views", "post">>("/page-views", "POST", body),

  // Settings, credentials and backups: the person's alone.
  updateSettings: (body: Schema["SettingsUpdate"]) =>
    request<Answer<"/api/v1/settings", "patch">>("/settings", "PATCH", body),
  issueCredential: (body: Schema["CredentialCreate"]) =>
    request<Answer<"/api/v1/credentials", "post">>(
      "/credentials",
      "POST",
      body,
    ),
  rotateCredential: (id: string, body: Schema["CredentialRotate"]) =>
    request<Answer<"/api/v1/credentials/{identity}/rotate", "post">>(
      `/credentials/${id}/rotate`,
      "POST",
      body,
    ),
  // The route takes no body; an empty one keeps the request what it was.
  revokeCredential: (id: string) =>
    request<Answer<"/api/v1/credentials/{identity}/revoke", "post">>(
      `/credentials/${id}/revoke`,
      "POST",
      {},
    ),
  // The route takes no body; an empty one keeps the request what it was.
  createBackup: () =>
    request<Answer<"/api/v1/backups", "post">>("/backups", "POST", {}),
};
