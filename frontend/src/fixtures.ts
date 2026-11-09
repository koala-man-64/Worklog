/**
 * Whole records for tests, each with every field its generated type has, so a test names only
 * the fields it is about. The rest are plain: no text, nothing archived, nothing tied.
 */
import type {
  AgentChat,
  Attachment,
  Note,
  Roadmap,
  RoadmapWorkItem,
  Step,
} from "./apiTypes";

/** When each record was made and last changed, unless a test says otherwise. */
export const MADE = "2026-11-01T09:00:00+00:00";

type Fields<T extends { id: string }> = Pick<T, "id"> & Partial<T>;

export const aRoadmap = (fields: Fields<Roadmap>): Roadmap => ({
  title: "",
  overview: "",
  parentId: null,
  revision: 1,
  archived: false,
  createdAt: MADE,
  updatedAt: MADE,
  steps: [],
  versions: [],
  notes: [],
  attachments: [],
  links: [],
  chats: [],
  github: [],
  questions: [],
  decisions: [],
  history: [],
  ancestors: [],
  children: [],
  workItems: [],
  people: [],
  imports: [],
  ...fields,
});

export const aStep = (fields: Fields<Step>): Step => ({
  title: "",
  description: "",
  parentId: null,
  position: 0,
  status: "open",
  revision: 1,
  retired: false,
  notes: [],
  attachments: [],
  ...fields,
});

/** A work item as a roadmap's page lists it; it serves wherever a work item is embedded. */
export const anItem = (fields: Fields<RoadmapWorkItem>): RoadmapWorkItem => ({
  title: "",
  description: "",
  status: "open",
  adoUrl: null,
  roadmapId: null,
  stepId: null,
  backlogOrder: 0,
  archived: false,
  revision: 1,
  createdAt: MADE,
  updatedAt: MADE,
  repositoryIds: [],
  people: [],
  ...fields,
});

export const aNote = (fields: Fields<Note>): Note => ({
  ownerType: "item",
  ownerId: "",
  title: "",
  text: "",
  archived: false,
  revision: 1,
  createdAt: MADE,
  updatedAt: MADE,
  ...fields,
});

export const anAttachment = (fields: Fields<Attachment>): Attachment => ({
  ownerType: "item",
  ownerId: "",
  name: "",
  mime: "application/octet-stream",
  size: 0,
  sha256: "",
  archived: false,
  revision: 1,
  createdAt: MADE,
  ...fields,
});

export const aChat = (fields: Fields<AgentChat>): AgentChat => ({
  ownerType: "item",
  ownerId: "",
  chatId: "",
  title: "",
  createdDate: "2026-11-01",
  archived: false,
  revision: 1,
  createdAt: MADE,
  updatedAt: MADE,
  provider: "",
  model: "",
  repos: [],
  stepIds: [],
  repositoryIds: [],
  ...fields,
});
