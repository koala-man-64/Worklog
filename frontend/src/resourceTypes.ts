/** A reference page as lists carry it: its title and what it is for, without its text. */
export type Resource = {
  id: string;
  title: string;
  /** What the page is for, in a sentence, kept as typed. */
  summary: string;
  archived: boolean;
  revision: number;
  createdAt: string;
  updatedAt: string;
};
/** A page with its Markdown text, as a create or an edit returns it. */
export type ResourcePage = Resource & { body: string };
/** Who changed a page and how, without the records: /history has those. */
export type ResourceChange = {
  id: string;
  kind: string;
  actorId: string;
  actorLabel: string;
  actorKind: "agent" | "human";
  createdAt: string;
};
export type ResourceDetail = ResourcePage & {
  /** The latest 100 changes, newest first. */
  history: ResourceChange[];
};
