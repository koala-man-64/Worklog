import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, download, request, getDataContext } from "./api";
import { useData, useWrites } from "./hooks";
import { commands } from "./commands";
import { AgentTies } from "./AgentTies";
import { Files } from "./sections/Files";
import { Markdown } from "./ui/Markdown";
import { Empty, ErrorMessage } from "./ui/Messages";
import { formatBytes } from "./ui/format";
import { Pager } from "./ui/Pager";
import type {
  Answer,
  Attachment,
  Day,
  Detail,
  Operation,
  Proposal,
  ProposalListed,
  Repository,
  RepositoryResolution,
  Settings,
} from "./apiTypes";
import { Icon } from "./icons";
import { statusLabel } from "./ProposalHistory";
// The list is paged 50 at a time, as Pages moves.
const PAGE = 50;
export function Review({ selectedId }: { selectedId?: string }) {
  const [status, setStatus] = useState("submitted");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({
    ...(status ? { status } : {}),
    limit: String(PAGE),
    offset: String(offset),
  });
  const proposals = useData<Answer<"/api/v1/proposals">>(
    "/proposals?" + params.toString(),
  );
  const total = proposals.data?.total ?? 0;
  const emptied =
    !!proposals.data && !proposals.data.items.length && offset > 0;
  useEffect(() => {
    // Reviewing the last proposals of a later page leaves it empty: show the last page there is.
    if (emptied) setOffset(Math.max(0, Math.floor((total - 1) / PAGE) * PAGE));
  }, [emptied, total]);
  const settings = useData<Settings>("/settings");
  const selected = selectedId ?? "";
  return (
    <>
      <header>
        <h1>Review</h1>
        <p className="lede">
          Inspect agent suggestions before they change your work.
        </p>
      </header>
      {settings.data?.requireProposalApproval === false && (
        <p className="warning">
          Approval is off: a proposal is applied as soon as its agent submits
          it. Turn approval back on in <a href="#settings">Settings</a>.
        </p>
      )}
      <div className="toolbar">
        <label>
          Proposal status
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
              location.hash = "review";
            }}
          >
            {[
              "submitted",
              "draft",
              "approved",
              "rejected",
              "withdrawn",
              "",
            ].map((s) => (
              <option value={s} key={s}>
                {s || "All proposals"}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrorMessage error={proposals.error} />
      {selected ? (
        <ProposalDetail
          id={selected}
          onBack={() => {
            location.hash = "review";
          }}
        />
      ) : !proposals.data?.items.length ? (
        <Empty>
          No {status} proposals. Your canonical work stays under your control.
        </Empty>
      ) : (
        proposals.data.items.map((p) => (
          <article className="roadmap-row" key={p.id}>
            <div>
              <a className="text-button item-title" href={"#review/" + p.id}>
                {p.title}
              </a>
              <p>
                {p.actorLabel}, {p.operations.length} proposed changes
              </p>
              <Directory proposal={p} />
              <Dates proposal={p} />
            </div>
            <span className="badge">{statusLabel(p)}</span>
          </article>
        ))
      )}
      {!selected && (
        <Pager
          offset={offset}
          total={total}
          onChange={setOffset}
          label="proposals"
        />
      )}
    </>
  );
}
function ProposalDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const query = useData<Proposal>("/proposals/" + id);
  const action = useWrites();
  const [decision, setDecision] = useState<
    "approve" | "reopen" | "reject" | null
  >(null);
  const [staleByOp, setStaleByOp] = useState<Record<string, string>>({});
  const p = query.data;
  const scope = p ? `${id}:${p.revision}` : id;
  const reportStale = useCallback(
    (number: number, detail: string | null) => {
      const key = `${scope}:${number}`;
      setStaleByOp((prev) => {
        if (detail === null) {
          if (!(key in prev)) return prev;
          const next = { ...prev };
          delete next[key];
          return next;
        }
        if (prev[key] === detail) return prev;
        return { ...prev, [key]: detail };
      });
    },
    [scope],
  );
  if (!p)
    return (
      <>
        <ErrorMessage error={query.error} />
        <p>Loading proposal…</p>
      </>
    );
  const declared = declaredTargets(p.operations);
  const staleList = Object.entries(staleByOp)
    .filter(([key]) => key.startsWith(scope + ":"))
    .sort(([a], [b]) => Number(a.split(":").pop()) - Number(b.split(":").pop()))
    .map(([, detail]) => detail);
  return (
    <section>
      <button className="quiet back" onClick={onBack}>
        <Icon name="back" />
        {/* Its own element, so the icon is not the button's only child and stays inline. */}
        <span>Proposal list</span>
      </button>
      <h2>{p.title}</h2>
      <p className="muted">
        Proposed by {p.actorLabel}, {statusLabel(p)}, revision {p.revision}
      </p>
      <Directory proposal={p} />
      <Dates proposal={p} className="muted" />
      <Markdown text={p.rationale} />
      <h3>Proposed changes</h3>
      {p.operations.map((op, index) => (
        <OperationDiff
          key={index}
          number={index + 1}
          action={op.action}
          payload={op.payload}
          files={p.attachments ?? []}
          proposedEarlier={(target) => (declared.get(target) ?? index) < index}
          createdBy={(name) => {
            const at = declared.get(name);
            return at !== undefined && at < index ? at + 1 : undefined;
          }}
          onStale={reportStale}
        />
      ))}
      {(p.attachments?.length ?? 0) > 0 && (
        <Files
          ownerType="proposal"
          ownerId={id}
          files={p.attachments ?? []}
          readOnly
        />
      )}
      <AgentTies targetType="proposal" targetId={id} />
      <ErrorMessage error={action.error} />
      {p.status === "submitted" && (
        <div className="decision">
          {staleList.length > 0 && (
            <p className="warning">
              Target record changed since this proposal was drafted:{" "}
              {staleList.join("; ")}. Return this proposal to draft to notify{" "}
              {p.actorLabel} to rebase and resubmit, or reject it.
            </p>
          )}
          <p>
            Approval applies all operations together. If any target has changed,
            the server keeps the proposal pending.
          </p>
          {decision ? (
            <>
              <strong>
                {decision === "approve"
                  ? "Apply all of these changes?"
                  : decision === "reopen"
                    ? "Return this proposal to draft and notify its agent?"
                    : "Reject this proposal?"}
              </strong>
              <div className="toolbar">
                <button
                  className={
                    decision === "approve"
                      ? "primary"
                      : decision === "reject"
                        ? "danger"
                        : ""
                  }
                  disabled={action.isPending}
                  onClick={async () => {
                    await action
                      .mutateAsync(() =>
                        commands.transitionProposal(id, decision, {
                          revision: p.revision,
                        }),
                      )
                      .then(() => setDecision(null))
                      .catch(() => undefined);
                  }}
                >
                  {decision === "reopen"
                    ? "Confirm return to draft"
                    : `Confirm ${decision}`}
                </button>
                <button onClick={() => setDecision(null)}>
                  Keep reviewing
                </button>
              </div>
            </>
          ) : (
            <div className="toolbar">
              <button
                className="primary"
                onClick={() => setDecision("approve")}
              >
                Approve proposal
              </button>
              <button onClick={() => setDecision("reopen")}>
                Return to draft
              </button>
              <button onClick={() => setDecision("reject")}>
                Reject proposal
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
function timestamp(value: string) {
  return <time dateTime={value}>{new Date(value).toLocaleString()}</time>;
}
// The directory the proposal's agent registered from, which says where it
// works; nothing when it named none.
function Directory({ proposal: p }: { proposal: ProposalListed }) {
  if (!p.actorDirectory) return null;
  return (
    <p className="muted proposal-directory">
      Registered from <code>{p.actorDirectory}</code>
    </p>
  );
}
// Dated by its submission, or by its start until it is submitted; "updated"
// shows only for a change after that: an edit, a withdrawal or a decision.
function Dates({
  proposal: p,
  className,
}: {
  proposal: ProposalListed;
  className?: string;
}) {
  const since = p.submittedAt ?? p.createdAt;
  if (!since) return null;
  return (
    <p className={className}>
      {p.submittedAt ? "Submitted " : "Drafted "}
      {timestamp(since)}
      {p.updatedAt && Date.parse(p.updatedAt) > Date.parse(since) && (
        <> · updated {timestamp(p.updatedAt)}</>
      )}
    </p>
  );
}
// A name a create gives its new record, such as @jane, which later changes in
// the same proposal use in place of its ID until approval gives it one. The
// server's rule (operations.NAME); it refuses a name nothing earlier declares.
const NAME = /^@[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
// The fields where a name can stand for a record's ID, as the server reads them.
const NAME_FIELDS = new Set([
  "id",
  "itemId",
  "roadmapId",
  "parentId",
  "personId",
  "releaseId",
  "ownerId",
  "targetId",
  "repositoryIds",
]);
function namesIn(key: string, value: unknown): string[] {
  if (!NAME_FIELDS.has(key)) return [];
  return (Array.isArray(value) ? value : [value]).filter(
    (v): v is string => typeof v === "string" && NAME.test(v),
  );
}
// What a later operation may target before approval makes it: a step whose ID
// roadmap.create or roadmap.revise chooses, or a record a create names. Maps
// each to the first operation that declares it.
function declaredTargets(operations: Operation[]) {
  const first = new Map<string, number>();
  operations.forEach(({ action, payload }, index) => {
    if (action.endsWith(".create") && namesIn("id", payload.id).length) {
      const name = payload.id as string;
      if (!first.has(name)) first.set(name, index);
    }
    if (action !== "roadmap.create" && action !== "roadmap.revise") return;
    if (!Array.isArray(payload.steps)) return;
    for (const step of payload.steps as unknown[]) {
      const id = (step as { id?: unknown } | null)?.id;
      if (typeof id === "string" && !first.has(id)) first.set(id, index);
    }
  });
  return first;
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
// A ticket is named by its UUID, its key (T-12) or its number.
const TICKET = /^(T-)?[1-9][0-9]{0,8}$/i;
// Proposal payloads are agent-controlled, so only well-formed IDs and dates may
// become URL path segments. Returns null when the target cannot be fetched safely.
function currentPath(
  action: string,
  payload: Record<string, unknown>,
  target: string,
): string | null {
  if (action.startsWith("ticket."))
    return UUID.test(target) || TICKET.test(target)
      ? "/tickets/" + encodeURIComponent(target)
      : null;
  if (!UUID.test(target)) return null;
  const id = encodeURIComponent(target);
  if (action === "entry.upsert") {
    const date = String(payload.date ?? "");
    return DATE.test(date) ? "/days/" + encodeURIComponent(date) : null;
  }
  if (action === "progress.set" || action.startsWith("step.")) return "/steps/" + id;
  if (action.startsWith("repository.")) return "/repositories/" + id;
  if (action.startsWith("release.")) return "/releases/" + id;
  if (action.startsWith("check.")) return "/release-checks/" + id;
  const collection = action.startsWith("roadmap.")
    ? "roadmaps"
    : action.startsWith("person.")
      ? "people"
      : action.startsWith("resource.")
        ? "resources"
        : action.startsWith("note.")
          ? "notes"
          : action.startsWith("task.")
            ? "tasks"
            : action.startsWith("link.")
              ? "links"
              : action.startsWith("chat.")
                ? "chats"
                : action.startsWith("github.")
                  ? "github"
                  : action.startsWith("question.")
                    ? "questions"
                    : action.startsWith("decision.")
                      ? "decisions"
                      : "work-items";
  return "/" + collection + "/" + id;
}
export function OperationDiff({
  number,
  action,
  payload,
  files,
  proposedEarlier,
  createdBy,
  onStale,
}: {
  // The change's place in the proposal, as a failed approval names it.
  number?: number;
  action: string;
  payload: Record<string, unknown>;
  // The proposal's own files, which an attachment.create copies onto a record.
  files?: Attachment[];
  // True when an earlier operation in the same proposal creates the target.
  proposedEarlier?: (target: string) => boolean;
  // The number of the earlier change that creates the record a name stands
  // for, or undefined when none does.
  createdBy?: (name: string) => number | undefined;
  onStale?: (number: number, detail: string | null) => void;
}) {
  const target = String(
    payload.id ?? payload.itemId ?? payload.roadmapId ?? payload.stepId ?? "",
  );
  const creating = action.endsWith(".create");
  // A name has no record to load yet: an earlier change creates it.
  const named = !creating && namesIn("id", target).length > 0;
  const lookup = !!target && !creating && !named;
  const path = lookup ? currentPath(action, payload, target) : null;
  const current = useQuery({
    queryKey: [
      getDataContext(),
      "proposal-current",
      action,
      target,
      payload.date,
    ],
    enabled: path !== null,
    queryFn: async () => {
      if (path === null) return null;
      if (action === "entry.upsert") {
        const day = await request<Day>(path);
        return (
          (day.entries.find((e) => e.itemId === target) as unknown as Record<
            string,
            unknown
          >) ?? null
        );
      }
      return request<Record<string, unknown>>(path);
    },
  });
  // A repository this proposal registers may already be registered: approval
  // then links to that record, which is the current value to compare with.
  const address =
    action === "repository.create" && typeof payload.url === "string"
      ? payload.url.trim()
      : "";
  const registration = useQuery({
    queryKey: [getDataContext(), "proposal-registration", address],
    enabled: address !== "",
    queryFn: () =>
      request<RepositoryResolution>(
        "/repositories/resolve?url=" + encodeURIComponent(address),
      ),
  });
  const holder = registration.data?.repository ?? null;
  // A note for a work item lands in its one scratchpad, so say whether it starts or extends it.
  const noteItem =
    action === "note.create" &&
    payload.ownerType === "item" &&
    typeof payload.ownerId === "string" &&
    UUID.test(payload.ownerId)
      ? payload.ownerId
      : "";
  const noteOwner = useQuery({
    queryKey: [getDataContext(), "proposal-note-owner", noteItem],
    enabled: noteItem !== "",
    queryFn: () =>
      request<Detail>("/work-items/" + encodeURIComponent(noteItem)),
  });
  const before =
    current.data ?? (holder as unknown as Record<string, unknown> | null);
  // Not found is expected only for a target that this proposal creates first;
  // any other missing target stays an error, since approval would fail on it.
  const created =
    (named ||
      (current.error instanceof ApiError && current.error.status === 404)) &&
    !!proposedEarlier?.(target);
  const loading = current.isFetching || registration.isFetching;
  // Nothing exists yet to compare with: a create with no registered record, or a target an
  // earlier change creates.
  const isNew = !before && !loading && (creating || created);
  const staleDetail =
    before &&
    payload.revision !== undefined &&
    before.revision !== payload.revision
      ? `Change ${number ?? "?"} (${action}: expected revision ${String(payload.revision)}, current revision ${String(before.revision)})`
      : null;
  useEffect(() => {
    if (number !== undefined && onStale) {
      onStale(number, staleDetail);
    }
  }, [number, onStale, staleDetail]);
  return (
    <article className="operation">
      <h4>
        {number ? `Change ${number}: ` : ""}
        <span>{action}</span>
      </h4>
      {holder && (
        <Registered holder={holder} name={namesIn("id", payload.id)[0] ?? ""} />
      )}
      <ErrorMessage error={registration.error} />
      {noteOwner.data && (
        <p className="muted">
          {noteOwner.data.notes.some((n) => !n.archived)
            ? "This work item has a scratchpad: approval adds this note to its end."
            : "Approval starts this work item's scratchpad with this note."}
        </p>
      )}
      <ErrorMessage error={noteOwner.error} />
      {action === "attachment.create" && files && (
        <EvidenceFile
          file={files.find((file) => file.id === payload.attachmentId)}
        />
      )}
      {created ? (
        <p className="muted">
          An earlier change in this proposal creates this target, so it has no
          current value until approval.
        </p>
      ) : (
        // A name nothing earlier creates is the id row's to warn about.
        <ErrorMessage error={current.error} />
      )}
      {lookup && path === null && (
        <p className="warning">
          Malformed target: this operation’s ID or date is not valid, so its
          current value was not loaded.
        </p>
      )}
      {before &&
        payload.revision !== undefined &&
        before.revision !== payload.revision && (
          <p className="warning">
            This target changed: expected revision {String(payload.revision)},
            current revision {String(before.revision)}. Approval will be checked
            by the server.
          </p>
        )}
      {isNew && !created && <p className="muted">New record</p>}
      {Object.entries(payload).map(([key, value]) => (
        <div className={isNew ? "diff-row new" : "diff-row"} key={key}>
          <strong>{key}</strong>
          {/* A new record has nothing to compare with, so its values take the row. */}
          {!isNew && (
            <div>
              <small>Current</small>
              <pre>
                {before && key in before
                  ? display(before[key])
                  : loading
                    ? "Loading…"
                    : "No current value"}
              </pre>
            </div>
          )}
          <div className="diff-after">
            <small>Proposed</small>
            <pre>{display(value)}</pre>
            <NameNotes
              names={namesIn(key, value)}
              declares={creating && key === "id"}
              createdBy={createdBy}
            />
          </div>
        </div>
      ))}
    </article>
  );
}
// A name stands for a record the same proposal creates, so say which change
// creates it, or, on the create itself, what the name is for.
function NameNotes({
  names,
  declares,
  createdBy,
}: {
  names: string[];
  declares: boolean;
  createdBy?: (name: string) => number | undefined;
}) {
  if (declares)
    return names.map((name) => (
      <p className="muted" key={name}>
        {name} names this new record for later changes in this proposal.
      </p>
    ));
  if (!createdBy) return null;
  return names.map((name) => {
    const by = createdBy(name);
    return by ? (
      <p className="muted" key={name}>
        {name}: created by change {by}
      </p>
    ) : (
      <p className="warning" key={name}>
        {name}: no earlier change in this proposal creates it, so approval
        fails.
      </p>
    );
  });
}
// The file a change attaches is a copy of one of the proposal's own files, so name it.
function EvidenceFile({ file }: { file?: Attachment }) {
  if (!file)
    return (
      <p className="warning">
        No file with this ID was uploaded to this proposal, so approval fails.
      </p>
    );
  const link = (
    <a href={download(`/attachments/${file.id}?download=1`)}>{file.name}</a>
  );
  return file.archived ? (
    <p className="warning">
      {link} was archived from this proposal, so approval fails.
    </p>
  ) : (
    <p className="muted">
      Approval attaches a copy of {link} ({formatBytes(file.size)}), from this
      proposal’s files.
    </p>
  );
}
// Registering a remote that is already registered links to its record, so say
// which, and that a name the registration gives stands for that record.
function Registered({ holder, name }: { holder: Repository; name: string }) {
  const link = <a href={"#repositories/" + holder.id}>{holder.name}</a>;
  return holder.archived ? (
    <p className="warning">
      Registered as {link}, which is archived: recover it, or approval fails.
    </p>
  ) : (
    <p className="muted">
      Already registered as {link}: approval links to it and adds nothing
      {name && <>, so {name} stands for it in later changes</>}.
    </p>
  );
}
function display(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
