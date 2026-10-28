import { request } from "./api";
import type { components } from "./generatedApi";
import type { Item, Note, Task, Roadmap, Entry } from "./types";
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
};
