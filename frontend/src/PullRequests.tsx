import { useState } from "react";
import { commands } from "./commands";
import { LinkPicker } from "./LinkPicker";
import { useData, useWrites } from "./hooks";
import type {
  ActivityPullRequest,
  Answer,
  Item,
  PullRequest,
  PullRequestActivity,
  RoadmapListed,
} from "./apiTypes";
import { Activity, activityRefresh, mergeText } from "./PullRequestActivity";
import { Empty, ErrorMessage } from "./ui/Messages";
import { Pager } from "./ui/Pager";
import { statusLabel } from "./ui/Status";

const PAGE_SIZE = 50;

type LinkedOwner =
  | { type: "item"; id: string; title: string }
  | { type: "roadmap"; id: string; title: string };

export function PullRequests() {
  const [offset, setOffset] = useState(0);
  const [filterDraft, setFilterDraft] = useState("");
  const [filter, setFilter] = useState("");
  const [owner, setOwner] = useState<LinkedOwner | null>(null);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [reference, setReference] = useState("");
  const [submittedBy, setSubmittedBy] = useState("");
  const action = useWrites();
  const query = `/pull-requests?limit=${PAGE_SIZE}&offset=${offset}${
    filter ? `&submittedBy=${encodeURIComponent(filter)}` : ""
  }`;
  const list = useData<Answer<"/api/v1/pull-requests">>(query);
  const [days, setDays] = useState(30);
  const activity = useData<PullRequestActivity>(
    `/pull-requests/activity?days=${days}`,
    activityRefresh,
  );
  const merges = new Map(
    (activity.data?.pullRequests ?? []).map((merge) => [merge.id, merge]),
  );

  return (
    <>
      <header>
        <h1>Pull requests</h1>
      </header>

      <ErrorMessage error={activity.error} />
      <Activity
        report={activity.data}
        days={days}
        onDays={setDays}
        stale={activity.isPlaceholderData}
      />

      <h2>Add pull request</h2>
      <form
        className="quick-add pull-request-form"
        aria-label="Add pull request"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!owner || !url.trim()) return;
          await action
            .mutateAsync(() =>
              commands.createGithubRef({
                ownerType: owner.type,
                ownerId: owner.id,
                kind: "pr",
                url: url.trim(),
                title: title.trim(),
                reference: reference.trim(),
                description: "",
                chatId: null,
                submittedBy: submittedBy.trim(),
              }),
            )
            .then(() => {
              setUrl("");
              setTitle("");
              setReference("");
              setSubmittedBy("");
              setOwner(null);
              setOffset(0);
            })
            .catch(() => undefined);
        }}
      >
        <label>
          Pull request URL
          <input
            placeholder="https://…"
            type="url"
            maxLength={2048}
            required
            value={url}
            onChange={(event) => setUrl(event.target.value)}
          />
        </label>
        <label>
          Pull request title
          <input
            maxLength={1000}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          PR number
          <input
            maxLength={300}
            value={reference}
            onChange={(event) => setReference(event.target.value)}
          />
        </label>
        <label>
          Submitted by
          <input
            placeholder="Name or handle"
            maxLength={200}
            value={submittedBy}
            onChange={(event) => setSubmittedBy(event.target.value)}
          />
        </label>
        <div
          className="inline pull-request-owner"
          role="group"
          aria-label="Link to work item or roadmap"
        >
          <LinkPicker<Item>
            action="Choose work item"
            label="Work item to link"
            placeholder="Find work…"
            path={(q) =>
              `/work-items?view=all&archived=false&limit=20&q=${encodeURIComponent(q)}`
            }
            linked={(item) => owner?.type === "item" && item.id === owner.id}
            option={(item) => (
              <>
                <span>{item.title}</span>
                <small>{statusLabel(item.status)}</small>
              </>
            )}
            none="No work items yet."
            onPick={(item) =>
              setOwner({ type: "item", id: item.id, title: item.title })
            }
            disabled={action.isPending}
          />
          <LinkPicker<RoadmapListed>
            action="Choose roadmap"
            label="Roadmap to link"
            placeholder="Find roadmaps…"
            path={(q) => `/roadmaps?archived=false&q=${encodeURIComponent(q)}`}
            linked={(roadmap) =>
              owner?.type === "roadmap" && roadmap.id === owner.id
            }
            option={(roadmap) => <span>{roadmap.title}</span>}
            none="No roadmaps yet."
            onPick={(roadmap) =>
              setOwner({ type: "roadmap", id: roadmap.id, title: roadmap.title })
            }
            disabled={action.isPending}
          />
          {owner && (
            <div className="inline muted">
              <span>{owner.title}</span>
              <button
                type="button"
                className="quiet"
                disabled={action.isPending}
                onClick={() => setOwner(null)}
              >
                Clear link
              </button>
            </div>
          )}
        </div>
        <button
          className="primary"
          type="submit"
          disabled={action.isPending || !owner || !url.trim()}
        >
          Add pull request
        </button>
        <ErrorMessage error={action.error} />
      </form>

      <div className="toolbar">
        <form
          className="inline"
          aria-label="Filter pull requests"
          onSubmit={(event) => {
            event.preventDefault();
            setFilter(filterDraft.trim());
            setOffset(0);
          }}
        >
          <input
            aria-label="Filter by submitter"
            placeholder="Filter by submitter…"
            maxLength={200}
            value={filterDraft}
            onChange={(event) => setFilterDraft(event.target.value)}
          />
          <button type="submit">Filter</button>
          {filter && (
            <button
              type="button"
              className="quiet"
              onClick={() => {
                setFilterDraft("");
                setFilter("");
                setOffset(0);
              }}
            >
              Clear filter
            </button>
          )}
        </form>
      </div>

      <ErrorMessage error={list.error} />
      {list.isPending ? (
        <p>Loading pull requests…</p>
      ) : !list.error && !list.data?.items.length ? (
        <Empty>
          {filter
            ? "No pull requests match this submitter."
            : "No pull requests registered yet."}
        </Empty>
      ) : null}
      {(list.data?.items ?? []).map((pullRequest) => (
        <PullRequestRow
          key={pullRequest.id}
          pullRequest={pullRequest}
          merge={merges.get(pullRequest.id)}
          onSaved={() => setOffset(0)}
        />
      ))}
      <Pager
        offset={offset}
        total={list.data?.total ?? 0}
        onChange={setOffset}
        label="pull requests"
        limit={PAGE_SIZE}
      />
    </>
  );
}

function PullRequestRow({
  pullRequest,
  merge,
  onSaved,
}: {
  pullRequest: PullRequest;
  merge: ActivityPullRequest | undefined;
  onSaved: () => void;
}) {
  const title = pullRequest.title || pullRequest.reference || pullRequest.url;
  const merged = mergeText(merge);
  const ownerHref =
    pullRequest.ownerType === "item"
      ? `#work/${pullRequest.ownerId}`
      : `#roadmaps/${pullRequest.ownerId}`;
  return (
    <article className="line pull-request-row" aria-label={`Pull request ${title}`}>
      <a href={pullRequest.url} target="_blank" rel="noopener noreferrer">
        {title} ↗
      </a>
      {pullRequest.reference && pullRequest.reference !== title && (
        <code>{pullRequest.reference}</code>
      )}
      <span>Submitted by: {pullRequest.submittedBy?.trim() || "Unknown"}</span>
      <span>
        {pullRequest.ownerType === "item" ? "Work item" : "Roadmap"}: {" "}
        <a href={ownerHref}>{pullRequest.ownerTitle}</a>
      </span>
      {merged && <small className="pull-request-merge">{merged}</small>}
      {pullRequest.chatTitle && <small>Chat: {pullRequest.chatTitle}</small>}
      <SubmitterEditor
        pullRequest={pullRequest}
        title={title}
        onSaved={onSaved}
      />
    </article>
  );
}

function SubmitterEditor({
  pullRequest,
  title,
  onSaved,
}: {
  pullRequest: PullRequest;
  title: string;
  onSaved: () => void;
}) {
  const [editor, setEditor] = useState<{
    revision: number;
    value: string;
  } | null>(null);
  const action = useWrites();
  const original = pullRequest.submittedBy ?? "";
  const currentEditor =
    editor?.revision === pullRequest.revision ? editor : null;
  const unchanged = (currentEditor?.value ?? original).trim() === original.trim();
  return (
    <>
      {currentEditor ? (
        <form
          className="inline"
          aria-label={`Edit submitter for ${title}`}
          onSubmit={async (event) => {
            event.preventDefault();
            if (currentEditor.revision !== pullRequest.revision || unchanged)
              return;
            await action
              .mutateAsync(() =>
                commands.updateGithubRef(pullRequest.id, {
                  revision: currentEditor.revision,
                  submittedBy: currentEditor.value.trim(),
                }),
              )
              .then(() => {
                setEditor(null);
                onSaved();
              })
              .catch(() => undefined);
          }}
        >
          <label>
            Submitted by
            <input
              aria-label="Submitted by"
              maxLength={200}
              value={currentEditor.value}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setEditor((draft) =>
                  draft?.revision === currentEditor.revision
                    ? { ...draft, value }
                    : draft,
                );
              }}
            />
          </label>
          <button type="submit" disabled={action.isPending || unchanged}>
            Save submitter
          </button>
          <button
            type="button"
            className="quiet"
            disabled={action.isPending}
            onClick={() => setEditor(null)}
          >
            Cancel
          </button>
        </form>
      ) : (
        <button
          type="button"
          className="quiet"
          onClick={() =>
            setEditor({
              revision: pullRequest.revision,
              value: original,
            })
          }
        >
          Edit submitter
        </button>
      )}
      <ErrorMessage error={action.error} />
    </>
  );
}
