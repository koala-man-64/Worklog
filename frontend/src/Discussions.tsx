import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getDataContext } from "./api";
import { ErrorMessage, Empty } from "./components";
import {
  ContextPicker,
  ContextReference,
  useContextSearch,
  type ContextChoice,
} from "./discussionContexts";
import { Composer } from "./DiscussionComposer";
import { DiscussionRolesPanel } from "./DiscussionRoles";
import { DiscussionThread } from "./DiscussionThread";
import { formatDiscussionDate } from "./discussionFormatting";
import { useAction, useData } from "./hooks";
import type { ChatOverview, List } from "./types";
import type {
  DiscussionContext,
  DiscussionList,
  DiscussionParticipants,
  DiscussionRoles,
  DiscussionSummary,
} from "./discussionTypes";

const PAGE = 50;
const ROLE_PAGE = 100;
const REFRESH_MS = 30_000;
const VIEWS = [
  ["all", "All"],
  ["awaiting", "Awaiting"],
  ["archived", "Archived"],
] as const;

type View = (typeof VIEWS)[number][0];

function when(iso: string | null | undefined) {
  return formatDiscussionDate(iso);
}

function viewPath(
  view: View,
  query: string,
  context: DiscussionContext | null,
  repo: string,
  offset: number,
) {
  const params = new URLSearchParams({
    view,
    q: query,
    limit: String(PAGE),
    offset: String(offset),
  });
  if (context) {
    params.set("contextType", context.type);
    params.set("contextId", context.id);
  }
  if (repo.trim()) params.set("repo", repo.trim());
  return "/discussions?" + params.toString();
}

export function Discussions({ selected }: { selected?: string }) {
  const [view, setView] = useState<View>("all");
  const [query, setQuery] = useState("");
  const [stableQuery, setStableQuery] = useState("");
  const [contextFilter, setContextFilter] = useState<ContextChoice | null>(
    null,
  );
  const [repoFilter, setRepoFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [roleOffset, setRoleOffset] = useState(0);
  const [selectedId, setSelectedId] = useState(selected ?? "");
  const [newPostOpen, setNewPostOpen] = useState(false);
  const action = useAction();
  const queryClient = useQueryClient();
  const list = useData<DiscussionList>(
    viewPath(view, stableQuery, contextFilter, repoFilter, offset),
    REFRESH_MS,
  );
  const participants = useData<DiscussionParticipants>(
    "/discussions/participants",
    REFRESH_MS,
  );
  const roles = useData<DiscussionRoles>(
    `/discussions/roles?limit=${ROLE_PAGE}&offset=${roleOffset}`,
    REFRESH_MS,
  );
  const chats = useData<List<ChatOverview>>("/chats?archived=false");
  const [contextQuery, setContextQuery] = useState("");
  const contextSearch = useContextSearch(contextQuery);
  const items = list.data?.items ?? [];

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setStableQuery(query.trim());
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    setSelectedId(selected ?? "");
  }, [selected]);

  const repoOptions = useMemo(
    () => [...new Set(items.flatMap((item) => item.repos))].sort(),
    [items],
  );

  function refresh() {
    void queryClient.invalidateQueries({
      predicate: (queryInfo) =>
        queryInfo.queryKey[0] === getDataContext() &&
        typeof queryInfo.queryKey[1] === "string" &&
        queryInfo.queryKey[1].startsWith("/discussions"),
    });
  }

  async function createDiscussion(body: Record<string, unknown>) {
    const result = (await action.mutateAsync({
      path: "/discussions",
      body,
    })) as { discussion: DiscussionSummary };
    setNewPostOpen(false);
    setSelectedId(result.discussion.id);
    window.history.replaceState(
      null,
      "",
      "#discussions/" + result.discussion.id,
    );
  }

  const selectedSummary = items.find(
    (discussion) => discussion.id === selectedId,
  );
  const total = list.data?.total ?? 0;
  const pageStart = total ? offset + 1 : 0;
  const pageEnd = Math.min(offset + PAGE, total);

  return (
    <div className="bounded-screen">
      <header>
        <h1>Discussions</h1>
        <p className="lede">
          Durable messages across work, roadmaps, agent chats, and repositories.
          Agent checks are opt-in; a post does not wake anyone.
        </p>
      </header>
      <ErrorMessage error={list.error || participants.error || chats.error} />
      <div className="split discussions-split">
        <section
          className="panel discussion-list-panel"
          role="region"
          aria-label="Discussion list"
        >
          <div className="panel-head">
            <h2>Threads</h2>
            <div className="toolbar compact">
              <button type="button" onClick={refresh}>
                Refresh
              </button>
              <button
                type="button"
                className="primary"
                onClick={() => setNewPostOpen(true)}
              >
                New post
              </button>
            </div>
          </div>
          <div className="discussion-filters">
            <div
              className="thread-filter"
              role="group"
              aria-label="Thread view"
            >
              {VIEWS.map(([value, label]) => (
                <button
                  type="button"
                  key={value}
                  aria-pressed={view === value}
                  onClick={() => {
                    setView(value);
                    setOffset(0);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <label>
              Search discussions
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search messages and titles…"
              />
            </label>
            <ContextPicker
              label="Filter by context"
              query={contextQuery}
              onQueryChange={setContextQuery}
              selected={contextFilter ? [contextFilter] : []}
              onAdd={(choice) => {
                setContextFilter(choice);
                setOffset(0);
                setContextQuery("");
              }}
              onRemove={() => {
                setContextFilter(null);
                setOffset(0);
              }}
              results={contextSearch.data?.items ?? []}
              loading={contextSearch.isFetching}
              single
            />
            <label>
              Filter by repository
              <input
                type="search"
                list="discussion-repositories"
                value={repoFilter}
                onChange={(event) => {
                  setRepoFilter(event.target.value);
                  setOffset(0);
                }}
                placeholder="Any repository"
              />
              <datalist id="discussion-repositories">
                {repoOptions.map((repo) => (
                  <option key={repo} value={repo} />
                ))}
              </datalist>
            </label>
          </div>
          <div className="panel-body">
            {list.isPending ? (
              <p>Loading discussions…</p>
            ) : !items.length ? (
              <Empty>
                {view === "awaiting"
                  ? "No acknowledgement requests are waiting."
                  : view === "archived"
                    ? "No archived discussions."
                    : "No discussions yet. Start a post when you have something to share or ask."}
              </Empty>
            ) : (
              <ul className="discussion-list">
                {items.map((discussion) => (
                  <DiscussionListItem
                    key={discussion.id}
                    discussion={discussion}
                    selected={discussion.id === selectedId}
                    onSelect={() => {
                      setSelectedId(discussion.id);
                      setNewPostOpen(false);
                    }}
                  />
                ))}
              </ul>
            )}
            {total > PAGE && (
              <div className="discussion-pagination">
                <button
                  type="button"
                  disabled={offset <= 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE))}
                >
                  Previous page
                </button>
                <small>
                  {pageStart}–{pageEnd} of {total}
                </small>
                <button
                  type="button"
                  disabled={offset + PAGE >= total}
                  onClick={() => setOffset(offset + PAGE)}
                >
                  Next page
                </button>
              </div>
            )}
            <DiscussionRolesPanel
              roles={roles.data?.items ?? []}
              total={roles.data?.total ?? 0}
              offset={roleOffset}
              limit={ROLE_PAGE}
              onPage={setRoleOffset}
              onCreated={() => setRoleOffset(0)}
              participants={participants.data}
              error={roles.error}
              isPending={roles.isPending}
            />
          </div>
        </section>
        <section
          className="pane detail discussion-detail"
          role="region"
          aria-label="Discussion"
        >
          {newPostOpen ? (
            <>
              <div className="discussion-detail-head">
                <h2>New post</h2>
                <button
                  type="button"
                  className="quiet"
                  onClick={() => setNewPostOpen(false)}
                >
                  Cancel
                </button>
              </div>
              <Composer
                key="new"
                scope="new"
                mode="new"
                participants={participants.data}
                roles={roles.data?.items ?? []}
                chats={chats.data?.items ?? []}
                onSubmit={createDiscussion}
              />
            </>
          ) : selectedId ? (
            <DiscussionThread
              key={selectedId}
              id={selectedId}
              summary={selectedSummary}
              participants={participants.data}
              roles={roles.data?.items ?? []}
              chats={chats.data?.items ?? []}
            />
          ) : (
            <Empty>
              Select a discussion or start a new post. Threads keep their
              history even when archived.
            </Empty>
          )}
        </section>
      </div>
    </div>
  );
}

function DiscussionListItem({
  discussion,
  selected,
  onSelect,
}: {
  discussion: DiscussionSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li className={selected ? "selected" : undefined}>
      <a
        className="discussion-list-link"
        href={"#discussions/" + discussion.id}
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
      >
        <strong>{discussion.title || "Untitled discussion"}</strong>
        <small>{when(discussion.updatedAt || discussion.createdAt)}</small>
      </a>
      {discussion.archived && (
        <span className="discussion-archived-label">Archived</span>
      )}
      {discussion.pendingCount > 0 && (
        <span className="discussion-pending-count">
          {discussion.pendingCount} awaiting acknowledgement
          {discussion.pendingCount === 1 ? "" : "s"}
        </span>
      )}
      {discussion.contexts.length > 0 && (
        <div className="discussion-contexts" aria-label="Linked context">
          {discussion.contexts.map((context) => (
            <ContextReference
              key={context.type + context.id}
              context={context}
            />
          ))}
        </div>
      )}
      {discussion.repos.length > 0 && (
        <div className="discussion-repos" aria-label="Repositories">
          {discussion.repos.map((repo) => (
            <span className="repo-tag" key={repo}>
              {repo}
            </span>
          ))}
        </div>
      )}
    </li>
  );
}
