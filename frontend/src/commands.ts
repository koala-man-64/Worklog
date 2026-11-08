import { request } from "./api";
import type { Answer, Schema } from "./apiTypes";
/** A command archives a record, or recovers it. */
const verb = (archived: boolean) => (archived ? "archive" : "recover");
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
};
