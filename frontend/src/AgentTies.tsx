import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getDataContext, request } from "./api";
import { Avatar } from "./Avatar";
import { ErrorMessage, TieRole } from "./components";
import { useAction, useData } from "./hooks";
import { LinkPicker } from "./LinkPicker";
import {
  TIE_LABELS,
  TIE_TYPES,
  archivedWord,
  pickedTitle,
  pickerFilter,
  pickerNone,
  pickerPath,
  tieHref,
  tiesOn,
  type AgentTie,
  type PickedRecord,
  type TieType,
} from "./agentTieTypes";
import type { DiscussionParticipants } from "./discussionTypes";
import type { List } from "./types";

// Agents tie themselves while a page is open, so the ties on it are read again now and then.
const REFRESH = 30_000;
const ROLE = "Role (e.g. Maintainer, Reviewer)…";
// The most records one read of ties asks about (backend agent_links.TARGET_IDS_LIMIT).
const BATCH = 200;

function setBy(tie: AgentTie) {
  return tie.setBy === "human" ? "By you" : "By the agent";
}
function agentKey(tie: AgentTie) {
  return {
    agentId: tie.agentId,
    targetType: tie.targetType,
    targetId: tie.targetId,
  };
}

/**
 * The agents tied to one record, each with its role there and who set the tie, and a form
 * that ties another. Agents whose credentials are all revoked sit folded below the rest.
 */
export function AgentTies({
  targetType,
  targetId,
  heading = "Agents",
  headingLevel = 3,
  canTie = true,
  disabled = false,
  layout = "section",
  note,
}: {
  targetType: TieType;
  targetId: string;
  heading?: string;
  /** A sentence on what a tie here does not do, such as on a chat, which ties never deliver to. */
  note?: string;
  /** 2 where the page's other sections are h2, as on a repository's page. */
  headingLevel?: 2 | 3;
  /** False keeps the ties' roles editable and their Unlink, but takes no new tie, as on an archived repository. */
  canTie?: boolean;
  disabled?: boolean;
  /** "fold" shows it as a disclosure, where the page's own content comes first; "bare" shows only the rows and form. */
  layout?: "section" | "fold" | "bare";
}) {
  const query = useData<List<AgentTie>>(
    tiesOn(targetType, [targetId]),
    REFRESH,
  );
  const [open, setOpen] = useState(false);
  const ties = query.data?.items ?? [];
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const title = (
    <>
      {heading} <span className="count">{query.data?.total ?? 0}</span>
    </>
  );
  const body = (
    <>
      {note && <p className="muted">{note}</p>}
      <ErrorMessage error={query.error} />
      <TieRows ties={ties} disabled={disabled} loaded={!!query.data} />
      {canTie && (
        <TieAgentForm
          targetType={targetType}
          targetId={targetId}
          ties={ties}
          disabled={disabled}
        />
      )}
    </>
  );
  if (layout === "bare") return body;
  // A fold keeps the page it sits on as it reads: its rows and form appear once it is opened.
  if (layout === "fold")
    return (
      <details
        className="agent-ties-fold"
        onToggle={(e) => setOpen(e.currentTarget.open)}
      >
        <summary>{title}</summary>
        {open && body}
      </details>
    );
  return (
    <section className="agent-ties-panel" aria-label={heading}>
      <Heading>{title}</Heading>
      {body}
    </section>
  );
}

function TieRows({
  ties,
  disabled,
  loaded,
}: {
  ties: AgentTie[];
  disabled: boolean;
  loaded: boolean;
}) {
  const live = ties.filter((tie) => tie.agent?.active);
  const retired = ties.filter((tie) => !tie.agent?.active);
  const row = (tie: AgentTie) => {
    const label = tie.agent?.label ?? tie.agentId;
    return (
      <div className="line agent-tie-row" key={tie.id}>
        <span className="agent-tie">
          <Avatar name={tie.agent?.avatar} size={18} />{" "}
          <a href={"#agents/" + tie.agentId}>{label}</a>
          {!tie.agent?.active && <small> (credential revoked)</small>}
        </span>
        <span className="badge">{setBy(tie)}</span>
        <TieRole
          path="/agent-links"
          tie={agentKey(tie)}
          name={"Role for " + label}
          unlinkName={"Unlink " + label}
          relationship={tie.relationship}
          disabled={disabled}
          unlinkDisabled={disabled}
          placeholder={ROLE}
        />
      </div>
    );
  };
  return (
    <>
      {loaded && !ties.length && <p className="muted">No agents tied.</p>}
      {live.map(row)}
      {retired.length > 0 && (
        <details className="retired-ties">
          <summary>Retired agents ({retired.length})</summary>
          {retired.map(row)}
        </details>
      )}
    </>
  );
}

function TieAgentForm({
  targetType,
  targetId,
  ties,
  disabled,
}: {
  targetType: TieType;
  targetId: string;
  ties: AgentTie[];
  disabled: boolean;
}) {
  const directory = useData<DiscussionParticipants>(
    "/discussions/participants",
  );
  const action = useAction();
  const [agentId, setAgentId] = useState("");
  const [relationship, setRelationship] = useState("");
  const tied = new Set(ties.map((tie) => tie.agentId));
  // An agent already tied changes its role in its own row; an agent is never tied to itself.
  const offered = (directory.data?.items ?? []).filter(
    (p) =>
      p.kind === "agent" &&
      p.active &&
      !tied.has(p.id) &&
      !(targetType === "agent" && p.id === targetId),
  );
  return (
    <>
      <form
        className="inline"
        onSubmit={(e) => {
          e.preventDefault();
          if (disabled || !agentId) return;
          action.mutate(
            {
              path: "/agent-links",
              body: {
                agentId,
                targetType,
                targetId,
                relationship: relationship.trim(),
              },
            },
            {
              onSuccess: () => {
                setAgentId("");
                setRelationship("");
              },
            },
          );
        }}
      >
        <select
          aria-label="Choose agent"
          value={agentId}
          disabled={disabled}
          onChange={(e) => setAgentId(e.target.value)}
        >
          <option value="">Select agent to tie…</option>
          {offered.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
        <input
          aria-label="Agent's role"
          placeholder={ROLE}
          value={relationship}
          disabled={disabled}
          onChange={(e) => setRelationship(e.target.value)}
        />
        <button
          type="submit"
          disabled={disabled || action.isPending || !agentId}
        >
          Tie agent
        </button>
      </form>
      <ErrorMessage error={action.error} />
    </>
  );
}

/** The ties on some records of one type, read at once, such as a roadmap's questions. */
export function useTiesOn(type: TieType, ids: string[]) {
  const asked = ids.slice(0, BATCH);
  const path = tiesOn(type, asked);
  const query = useQuery({
    queryKey: [getDataContext(), path],
    queryFn: () => request<List<AgentTie>>(path),
    enabled: asked.length > 0,
    refetchInterval: REFRESH,
  });
  const byTarget = new Map<string, AgentTie[]>();
  for (const tie of query.data?.items ?? [])
    byTarget.set(tie.targetId, [...(byTarget.get(tie.targetId) ?? []), tie]);
  return byTarget;
}

/**
 * A row's tied agents as chips, and a fold that ties, re-roles and unties them, for a
 * record shown in a list rather than on a page of its own, such as a question.
 */
export function AgentTieFold({
  targetType,
  targetId,
  title,
  ties = [],
  disabled = false,
}: {
  targetType: TieType;
  targetId: string;
  /** Names the fold for assistive technology, such as the question's title. */
  title: string;
  ties?: AgentTie[];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (disabled && !ties.length) return null;
  return (
    <div className="agent-tie-fold">
      {ties.length > 0 && (
        <span className="person-badges" aria-label={"Agents tied to " + title}>
          {ties.map((tie) => (
            <a
              key={tie.id}
              className="person-badge"
              href={"#agents/" + tie.agentId}
            >
              {tie.agent?.label ?? tie.agentId}
              {tie.relationship ? ` (${tie.relationship})` : ""}
            </a>
          ))}
        </span>
      )}
      <details onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary aria-label={"Agents on " + title}>Agents</summary>
        {open && (
          <AgentTies
            targetType={targetType}
            targetId={targetId}
            layout="bare"
            canTie={!disabled}
            disabled={disabled}
          />
        )}
      </details>
    </div>
  );
}

/**
 * Everything one agent is tied to, by type, each with its role there and who set the
 * tie. The person ties the agent to any record from here too.
 */
export function AgentTiedTo({ agentId }: { agentId: string }) {
  const query = useData<List<AgentTie>>(
    "/agent-links?agentId=" + encodeURIComponent(agentId),
    REFRESH,
  );
  const ties = query.data?.items ?? [];
  const groups = TIE_TYPES.map(
    (type) => [type, ties.filter((tie) => tie.targetType === type)] as const,
  ).filter(([, list]) => list.length);
  return (
    <section aria-label="Tied to">
      <h2>Tied to</h2>
      <p className="muted">
        The records you tied this agent to, and those it tied itself to, with
        its role on each. It reads these with its context.
      </p>
      <ErrorMessage error={query.error} />
      {query.data && !ties.length && (
        <p className="muted">Not tied to anything yet.</p>
      )}
      {groups.map(([type, list]) => (
        <section
          className="agent-ties"
          key={type}
          aria-label={"Tied " + TIE_LABELS[type].many.toLowerCase()}
        >
          <h3>
            {TIE_LABELS[type].many} <span className="count">{list.length}</span>
          </h3>
          {list.map((tie) => (
            <TiedRecord key={tie.id} tie={tie} />
          ))}
        </section>
      ))}
      {query.data && query.data.total > ties.length && (
        <p className="muted">
          Showing {ties.length} of {query.data.total}.
        </p>
      )}
      <TieRecordForm agentId={agentId} ties={ties} />
    </section>
  );
}

function TiedRecord({ tie }: { tie: AgentTie }) {
  const target = tie.target;
  const title = target?.title ?? tie.targetId;
  return (
    <div className="line agent-tie-row">
      <span className="agent-tie">
        <a href={tieHref(tie.targetType, tie.targetId, target)}>{title}</a>
        {target?.number != null && <small> T-{target.number}</small>}
        {target?.roadmapId && target.roadmapTitle && (
          <a className="person-badge" href={"#roadmaps/" + target.roadmapId}>
            {target.roadmapTitle}
          </a>
        )}
        {target?.archived && <small> ({archivedWord(tie.targetType)})</small>}
      </span>
      <span className="badge">{setBy(tie)}</span>
      <TieRole
        path="/agent-links"
        tie={agentKey(tie)}
        name={"Role on " + title}
        unlinkName={"Unlink " + title}
        relationship={tie.relationship}
        placeholder={ROLE}
      />
    </div>
  );
}

function TieRecordForm({
  agentId,
  ties,
}: {
  agentId: string;
  ties: AgentTie[];
}) {
  const [type, setType] = useState<TieType>("item");
  const [relationship, setRelationship] = useState("");
  const action = useAction();
  const tied = new Set(
    ties.filter((tie) => tie.targetType === type).map((tie) => tie.targetId),
  );
  return (
    <>
      <div className="inline agent-tie-add">
        <select
          aria-label="Type of record to tie"
          value={type}
          onChange={(e) => setType(e.target.value as TieType)}
        >
          {TIE_TYPES.map((t) => (
            <option key={t} value={t}>
              {TIE_LABELS[t].one}
            </option>
          ))}
        </select>
        <input
          aria-label="Role on the record"
          placeholder={ROLE}
          value={relationship}
          onChange={(e) => setRelationship(e.target.value)}
        />
        <LinkPicker<PickedRecord>
          key={type}
          action="Tie to a record"
          label={TIE_LABELS[type].one}
          placeholder={`Search ${TIE_LABELS[type].many.toLowerCase()}…`}
          path={(q) => pickerPath(type, q)}
          filter={pickerFilter(type, agentId)}
          linked={(record) =>
            tied.has(record.id) || (type === "agent" && record.id === agentId)
          }
          option={(record) => pickedTitle(record)}
          none={pickerNone(type)}
          disabled={action.isPending}
          onPick={(record) =>
            action.mutate(
              {
                path: "/agent-links",
                body: {
                  agentId,
                  targetType: type,
                  targetId: record.id,
                  relationship: relationship.trim(),
                },
              },
              { onSuccess: () => setRelationship("") },
            )
          }
        />
      </div>
      <ErrorMessage error={action.error} />
    </>
  );
}
