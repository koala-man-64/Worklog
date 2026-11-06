import { useEffect, useState } from "react";
import { AgentTies } from "./AgentTies";
import { contextStorageKey } from "./api";
import { commands } from "./commands";
import { Pages } from "./Coordination";
import {
  ChangeHistory,
  DraftField,
  Empty,
  ErrorMessage,
  Markdown,
} from "./components";
import {
  exactDiscussionDate,
  formatDiscussionDate,
} from "./discussionFormatting";
import { useAction, useCommand, useData } from "./hooks";
import { localStore } from "./storage";
import type { List } from "./types";
import type { Resource, ResourceDetail } from "./resourceTypes";

// Pages advances 50 at a time.
const PAGE = 50;
const FIELDS = ["title", "summary", "body"] as const;
const draftKey = (field: (typeof FIELDS)[number], id: string) =>
  `resource-${field}-${id}`;
/** A field of this page holds a draft the server has not taken yet. */
const unsaved = (id: string) =>
  FIELDS.some(
    (field) =>
      localStore.read(contextStorageKey("draft", draftKey(field, id))).value !==
      null,
  );
/** Headings carry `resources/<id>-<slug>` ids, as the Markdown gives them. */
function show(id: string, section: string) {
  let slug = section;
  try {
    slug = decodeURIComponent(section);
  } catch {
    // A malformed escape names no heading; look for it as written.
  }
  document
    .getElementById(`resources/${id}-${slug}`)
    ?.scrollIntoView({ block: "start" });
}

/**
 * Reference pages the person writes for themselves and their agents, beside the page they
 * open: `#resources/<id>` opens one and `#resources/<id>/<slug>` one of its sections.
 */
export function Resources({
  selected,
  section,
}: {
  selected?: string;
  section?: string;
}) {
  const [archived, setArchived] = useState(false);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  // The page being edited; any other page opens to be read.
  const [editing, setEditing] = useState<string | null>(null);
  const list = useData<List<Resource>>(
    `/resources?archived=${archived}&q=${encodeURIComponent(q)}&limit=${PAGE}&offset=${offset}`,
  );
  const items = list.data?.items ?? [];
  // With nothing chosen, the first page in the list opens.
  const current = selected ?? items[0]?.id;
  const nothingYet = !archived && !q && list.data?.total === 0;
  return (
    <div className="bounded-screen">
      <header>
        <h1>Resources</h1>
        <p className="lede">
          Reference pages you write for yourself and your agents. Agents read
          them with <code>worklog agent resources</code> and change them only
          through proposals.
        </p>
      </header>
      <ErrorMessage error={list.error} />
      <div className="split resources-split">
        <div className="panel" role="region" aria-label="Pages">
          <div className="panel-head">
            <h2>Pages</h2>
            <label className="muted">
              <input
                type="checkbox"
                checked={archived}
                onChange={(e) => {
                  setArchived(e.target.checked);
                  setOffset(0);
                }}
              />{" "}
              Show archive
            </label>
          </div>
          <div className="resource-tools">
            {!archived && (
              <NewResource
                created={(id) => {
                  setEditing(id);
                  location.hash = "#resources/" + id;
                }}
              />
            )}
            <input
              type="search"
              aria-label="Find pages"
              placeholder="Find pages…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOffset(0);
              }}
            />
          </div>
          <div className="panel-body">
            {list.isPending ? (
              <p>Loading pages…</p>
            ) : !items.length ? (
              <p className="muted resource-none">
                {archived
                  ? "No archived pages."
                  : q
                    ? "No page matches those words."
                    : "No pages yet."}
              </p>
            ) : (
              <ul className="resource-list">
                {items.map((r) => (
                  <li key={r.id}>
                    <a
                      href={"#resources/" + r.id}
                      aria-current={r.id === current ? "page" : undefined}
                    >
                      <span className="resource-name">{r.title}</span>
                      {r.summary.trim() && <small>{r.summary}</small>}
                    </a>
                  </li>
                ))}
              </ul>
            )}
            <Pages
              offset={offset}
              total={list.data?.total ?? 0}
              change={setOffset}
              label="pages"
            />
          </div>
        </div>
        <div
          className="pane detail resource-pane"
          role="region"
          aria-label="Page"
        >
          {current ? (
            <PageView
              key={current}
              id={current}
              section={current === selected ? section : undefined}
              editing={editing === current}
              setEditing={(on) => setEditing(on ? current : null)}
            />
          ) : nothingYet ? (
            <Empty>
              Write down what you and your agents keep looking up: how you work,
              conventions, runbooks. Name a page under Pages to start.
            </Empty>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** Adds a page by its title, then opens it to be written. */
function NewResource({ created }: { created: (id: string) => void }) {
  const [title, setTitle] = useState("");
  const create = useCommand(commands.createResource);
  return (
    <form
      className="resource-add"
      onSubmit={(e) => {
        e.preventDefault();
        if (!title.trim()) return;
        create.mutate(
          { title: title.trim(), summary: "", body: "" },
          {
            onSuccess: (page) => {
              setTitle("");
              created(page.id);
            },
          },
        );
      }}
    >
      <input
        aria-label="New page title"
        placeholder="New page title…"
        value={title}
        maxLength={1000}
        onChange={(e) => setTitle(e.target.value)}
      />
      <button className="primary" disabled={create.isPending || !title.trim()}>
        Add page
      </button>
      <ErrorMessage error={create.error} />
    </form>
  );
}

/**
 * One page, to read or to edit. A page whose draft is not saved yet stays in its editor,
 * so "Done" closes the editor only once every field is saved, and a conflict stays in view.
 * Typing again after "Done" keeps the editor open, so a later save never closes it mid-edit.
 */
function PageView({
  id,
  section,
  editing,
  setEditing,
}: {
  id: string;
  section?: string;
  editing: boolean;
  setEditing: (on: boolean) => void;
}) {
  const query = useData<ResourceDetail>("/resources/" + id);
  const action = useAction();
  const page = query.data;
  const editor = page !== undefined && (editing || unsaved(id));
  const loaded = page !== undefined;
  useEffect(() => {
    if (loaded && section && !editor) show(id, section);
  }, [id, section, loaded, editor]);
  if (!page)
    return (
      <>
        <ErrorMessage error={query.error} />
        {!query.error && <p>Loading page…</p>}
      </>
    );
  return (
    <article className="resource" aria-labelledby={"resource-" + id}>
      <header className="title-row resource-head">
        <h2 id={"resource-" + id}>{page.title}</h2>
        <div className="toolbar">
          {editor ? (
            <button className="primary" onClick={() => setEditing(false)}>
              Done
            </button>
          ) : (
            !page.archived && (
              <button onClick={() => setEditing(true)}>Edit page</button>
            )
          )}
          <button
            disabled={action.isPending}
            onClick={() =>
              action.mutate({
                path: `/resources/${id}/${page.archived ? "recover" : "archive"}`,
                body: { revision: page.revision },
              })
            }
          >
            {page.archived ? "Recover" : "Archive"}
          </button>
        </div>
      </header>
      <ErrorMessage error={action.error} />
      {page.archived && (
        <p className="warning">
          Archived: this page is out of the list and out of your agents’
          context, but they can still open it by its ID. Recover it to change
          it.
        </p>
      )}
      {editor ? (
        <div className="resource-editor" onInput={() => setEditing(true)}>
          <DraftField
            draftKey={draftKey("title", id)}
            label="Title"
            initial={page.title}
            revision={page.revision}
            multiline={false}
            maxLength={1000}
            disabled={page.archived}
            save={(title, revision) =>
              commands.updateResource(id, { revision, title })
            }
          />
          <DraftField
            draftKey={draftKey("summary", id)}
            label="Summary"
            initial={page.summary}
            revision={page.revision}
            multiline={false}
            maxLength={2000}
            disabled={page.archived}
            placeholder="What this page is for, in a sentence"
            save={(summary, revision) =>
              commands.updateResource(id, { revision, summary })
            }
          />
          <DraftField
            draftKey={draftKey("body", id)}
            label="Page (Markdown)"
            initial={page.body}
            revision={page.revision}
            disabled={page.archived}
            placeholder="Write the page: headings, lists, links and code blocks all work."
            save={(body, revision) =>
              commands.updateResource(id, { revision, body })
            }
          />
        </div>
      ) : (
        <>
          {page.summary.trim() && <p className="lede">{page.summary}</p>}
          <Markdown text={page.body} anchors={"resources/" + id} />
        </>
      )}
      <p className="muted resource-updated">
        Updated{" "}
        <time
          dateTime={page.updatedAt}
          title={exactDiscussionDate(page.updatedAt)}
        >
          {formatDiscussionDate(page.updatedAt)}
        </time>
      </p>
      <AgentTies targetType="resource" targetId={id} layout="fold" />
      <ChangeHistory id={id} changes={page.history.length} />
    </article>
  );
}
