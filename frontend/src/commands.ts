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
} from "./types";
type Schema = components["schemas"];
/** Actual command payloads are checked against the exported backend OpenAPI schema. */
export const commands = {
  createItem: (body: Schema["ItemCreate"]) =>
    request<Item>("/work-items", "POST", body),
  updateItem: (id: string, body: Schema["ItemUpdate"]) =>
    request<Item>("/work-items/" + id, "PATCH", body),
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
  createPerson: (body: Schema["PersonCreate"]) =>
    request<Person>("/people", "POST", body),
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
};
