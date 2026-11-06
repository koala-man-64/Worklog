import { request } from "./api";
import type { components } from "./generatedApi";
import type {
  Item,
  Note,
  Task,
  Roadmap,
  Entry,
  Person,
  PersonAssignment,
  WebLink,
  AgentChat,
  GithubRef,
  RankResult,
} from "./types";
import type { Release, ReleaseCheck } from "./releaseTypes";
import type { ReminderDetail } from "./reminderTypes";
import type { ResourcePage } from "./resourceTypes";
import type { Ticket, TicketComment } from "./ticketTypes";
type Schema = components["schemas"];
/** Actual command payloads are checked against the exported backend OpenAPI schema. */
export const commands = {
  createItem: (body: Schema["ItemCreate"]) =>
    request<Item>("/work-items", "POST", body),
  updateItem: (id: string, body: Schema["ItemUpdate"]) =>
    request<Item>("/work-items/" + id, "PATCH", body),
  rankItem: (id: string, body: Schema["Rank"]) =>
    request<RankResult>(`/work-items/${id}/rank`, "POST", body),
  upsertEntry: (date: string, id: string, body: Schema["EntryUpdate"]) =>
    request<Entry>(`/days/${date}/entries/${id}`, "PUT", body),
  createNote: (body: Schema["NoteCreate"]) =>
    request<Note>("/notes", "POST", body),
  updateNote: (id: string, body: Schema["NoteUpdate"]) =>
    request<Note>("/notes/" + id, "PATCH", body),
  createTask: (body: Schema["TaskCreate"]) =>
    request<Task>("/tasks", "POST", body),
  updateTask: (id: string, body: Schema["TaskUpdate"]) =>
    request<Task>("/tasks/" + id, "PATCH", body),
  createRoadmap: (body: Schema["RoadmapCreate"]) =>
    request<Roadmap>("/roadmaps", "POST", body),
  reviseRoadmap: (id: string, body: Schema["RoadmapRevision"]) =>
    request<Roadmap>(`/roadmaps/${id}/revisions`, "POST", body),
  /** Fields left out start empty. */
  createPerson: (
    body: Pick<Schema["PersonCreate"], "name"> &
      Partial<Schema["PersonCreate"]>,
  ) => request<Person>("/people", "POST", body),
  updatePerson: (id: string, body: Schema["PersonUpdate"]) =>
    request<Person>("/people/" + id, "PATCH", body),
  linkPerson: (body: Schema["PersonLinkCreate"]) =>
    request<PersonAssignment>("/person-links", "POST", body),
  unlinkPerson: (body: Schema["PersonLinkRemove"]) =>
    request<{ ok: boolean }>("/person-links/unlink", "POST", body),
  createLink: (body: Schema["LinkCreate"]) =>
    request<WebLink>("/links", "POST", body),
  updateLink: (id: string, body: Schema["LinkUpdate"]) =>
    request<WebLink>("/links/" + id, "PATCH", body),
  createChat: (body: Schema["ChatCreate"]) =>
    request<AgentChat>("/chats", "POST", body),
  updateChat: (id: string, body: Schema["ChatUpdate"]) =>
    request<AgentChat>("/chats/" + id, "PATCH", body),
  createGithubRef: (body: Schema["GithubRefCreate"]) =>
    request<GithubRef>("/github", "POST", body),
  updateGithubRef: (id: string, body: Schema["GithubRefUpdate"]) =>
    request<GithubRef>("/github/" + id, "PATCH", body),
  createTicket: (body: Schema["TicketCreate"]) =>
    request<Ticket>("/tickets", "POST", body),
  updateTicket: (id: string, body: Schema["TicketUpdate"]) =>
    request<Ticket>("/tickets/" + id, "PATCH", body),
  commentOnTicket: (id: string, body: Schema["TicketCommentCreate"]) =>
    request<TicketComment>(`/tickets/${id}/comments`, "POST", body),
  createResource: (body: Schema["ResourceCreate"]) =>
    request<ResourcePage>("/resources", "POST", body),
  updateResource: (id: string, body: Schema["ResourceUpdate"]) =>
    request<ResourcePage>("/resources/" + id, "PATCH", body),
  /** Fields left out take their defaults: no date, planned. */
  createRelease: (
    body: Pick<Schema["ReleaseCreate"], "title"> &
      Partial<Schema["ReleaseCreate"]>,
  ) => request<Release>("/releases", "POST", body),
  updateRelease: (id: string, body: Schema["ReleaseUpdate"]) =>
    request<Release>("/releases/" + id, "PATCH", body),
  /** Holds a work item, roadmap, step or GitHub reference; holding one twice changes nothing. */
  holdInRelease: (body: Schema["ReleaseContentChange"]) =>
    request<{ changed: boolean }>("/release-contents", "POST", body),
  removeFromRelease: (body: Schema["ReleaseContentChange"]) =>
    request<{ changed: boolean }>("/release-contents/remove", "POST", body),
  createReleaseCheck: (
    body: Pick<Schema["ReleaseCheckCreate"], "releaseId" | "kind" | "text"> &
      Partial<Schema["ReleaseCheckCreate"]>,
  ) => request<ReleaseCheck>("/release-checks", "POST", body),
  updateReleaseCheck: (id: string, body: Schema["ReleaseCheckUpdate"]) =>
    request<ReleaseCheck>("/release-checks/" + id, "PATCH", body),
  createReminder: (body: Schema["ReminderCreate"]) =>
    request<ReminderDetail>("/reminders", "POST", body),
  updateReminder: (id: string, body: Schema["ReminderUpdate"]) =>
    request<ReminderDetail>("/reminders/" + id, "PATCH", body),
};
