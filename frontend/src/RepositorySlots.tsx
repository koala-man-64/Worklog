import { useId, useState, type FormEvent } from "react";
import { commands } from "./commands";
import { useData, useWrites } from "./hooks";
import { CopyButton } from "./ui/CopyButton";
import { ErrorMessage } from "./ui/Messages";
import type {
  DispatchItem,
  DispatchQueue,
  Lease,
  Repository,
  RepositorySlot,
  RepositorySlotSummary,
  RepositorySlots,
  SlotPolicy,
} from "./apiTypes";

const PROVIDER_LABELS: Record<string, string> = {
  claude: "Claude",
  codex: "Codex",
  antigravity: "Antigravity",
};

function providerLabel(provider: string) {
  const key = provider.trim().toLowerCase();
  return PROVIDER_LABELS[key] ?? (provider || "Agent");
}

function slotStateLabel(state: RepositorySlot["state"]) {
  switch (state) {
    case "free":
      return "Free";
    case "offered":
      return "Offered";
    case "active":
      return "Active";
    case "expired_dirty":
      return "Expired (dirty)";
  }
}

function ttlRemaining(expiresAt: string | null) {
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (!Number.isFinite(ms)) return null;
  if (ms <= 0) return "Expired";
  const mins = Math.ceil(ms / 60_000);
  if (mins < 60) return `${mins}m left`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem > 0 ? `${hrs}h ${rem}m left` : `${hrs}h left`;
}

export function SlotSummaryPills({
  summary,
}: {
  summary?: RepositorySlotSummary | null;
}) {
  if (!summary) return null;
  const frozen = summary.maxActiveLeases === 0;
  const full = !frozen && summary.activeCount >= summary.maxActiveLeases;
  return (
    <span className="slot-pills" aria-label="Checkout slot status">
      <span
        className={`badge slot-pill${frozen ? " slot-frozen" : full ? " slot-full" : ""}`}
      >
        {frozen
          ? "Frozen (0/0)"
          : `Slots ${summary.activeCount}/${summary.maxActiveLeases}`}
      </span>
      {summary.queuedCount > 0 && (
        <span className="badge slot-pill slot-queued">
          +{summary.queuedCount} queued
        </span>
      )}
      {summary.dirtyCount > 0 && (
        <span className="badge slot-pill slot-dirty">
          {summary.dirtyCount} dirty
        </span>
      )}
      {summary.rescuedCount > 0 && (
        <span className="badge slot-pill">
          {summary.rescuedCount} rescued
        </span>
      )}
      {summary.baseReadonly && (
        <span className="badge slot-pill slot-locked">Base locked</span>
      )}
    </span>
  );
}

function SlotPolicyForm({
  repo,
  policy,
  action,
}: {
  repo: Repository;
  policy: SlotPolicy;
  action: ReturnType<typeof useWrites>;
}) {
  const [maxActive, setMaxActive] = useState(String(policy.maxActiveLeases));
  const [ttlSeconds, setTtlSeconds] = useState(
    String(policy.defaultTtlSeconds),
  );
  const [baseCheckoutPath, setBaseCheckoutPath] = useState(
    policy.baseCheckoutPath,
  );
  const [slotRootPath, setSlotRootPath] = useState(policy.slotRootPath);
  const [defaultBranch, setDefaultBranch] = useState(policy.defaultBranch);
  const [autoRescueDirty, setAutoRescueDirty] = useState(
    policy.autoRescueDirty,
  );

  function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsedMax = Number.parseInt(maxActive, 10);
    const parsedTtl = Number.parseInt(ttlSeconds, 10);
    action.mutate(() =>
      commands.updateSlotPolicy(repo.id, {
        revision: policy.revision,
        maxActiveLeases: Number.isFinite(parsedMax)
          ? parsedMax
          : policy.maxActiveLeases,
        defaultTtlSeconds: Number.isFinite(parsedTtl)
          ? parsedTtl
          : policy.defaultTtlSeconds,
        baseCheckoutPath: baseCheckoutPath.trim(),
        slotRootPath: slotRootPath.trim(),
        defaultBranch: defaultBranch.trim() || "main",
        autoRescueDirty,
      }),
    );
  }

  return (
    <form className="slot-policy-form" onSubmit={savePolicy}>
      <label>
        Max active slots
        <input
          aria-label="Max active slots"
          type="number"
          min={0}
          max={20}
          value={maxActive}
          disabled={repo.archived || action.isPending}
          onChange={(e) => setMaxActive(e.target.value)}
        />
      </label>
      <label>
        Default TTL (seconds)
        <input
          aria-label="Default TTL (seconds)"
          type="number"
          min={60}
          max={86400}
          value={ttlSeconds}
          disabled={repo.archived || action.isPending}
          onChange={(e) => setTtlSeconds(e.target.value)}
        />
      </label>
      <label>
        Default branch
        <input
          aria-label="Default branch"
          value={defaultBranch}
          maxLength={200}
          disabled={repo.archived || action.isPending}
          onChange={(e) => setDefaultBranch(e.target.value)}
        />
      </label>
      <label>
        Base checkout path
        <input
          aria-label="Base checkout path"
          placeholder="C:\\Projects\\my-repo"
          value={baseCheckoutPath}
          maxLength={2000}
          disabled={repo.archived || action.isPending}
          onChange={(e) => setBaseCheckoutPath(e.target.value)}
        />
      </label>
      <label>
        Slot root path (optional)
        <input
          aria-label="Slot root path"
          placeholder="Default: <base-parent>\\.worklog-slots"
          value={slotRootPath}
          maxLength={2000}
          disabled={repo.archived || action.isPending}
          onChange={(e) => setSlotRootPath(e.target.value)}
        />
      </label>
      <div className="slot-policy-actions">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={autoRescueDirty}
            disabled={repo.archived || action.isPending}
            onChange={(e) => setAutoRescueDirty(e.target.checked)}
          />
          Auto-rescue dirty expired slots to refs/worklog-rescue
        </label>
        <button
          type="submit"
          className="primary"
          disabled={repo.archived || action.isPending}
        >
          Save slot policy
        </button>
      </div>
    </form>
  );
}

function SlotCard({
  slot,
  repo,
  action,
}: {
  slot: RepositorySlot;
  repo: Repository;
  action: ReturnType<typeof useWrites>;
}) {
  const pathId = useId();
  const lease: Lease | null = slot.lease;
  const path = lease?.worktreePath || slot.expectedWorktreePath;
  const ttl = lease ? ttlRemaining(lease.expiresAt) : null;

  return (
    <article className={`slot-card state-${slot.state}`}>
      <div className="slot-card-head">
        <strong>Slot {slot.slotIndex}</strong>
        <span
          className={`badge slot-pill${
            slot.state === "free"
              ? " slot-free"
              : slot.state === "expired_dirty"
                ? " slot-dirty"
                : slot.state === "offered"
                  ? " slot-queued"
                  : ""
          }`}
        >
          {slotStateLabel(slot.state)}
        </span>
      </div>
      {path && (
        <div className="slot-card-path">
          <code id={pathId}>{path}</code>
          <CopyButton
            targetId={pathId}
            label={`Copy slot ${slot.slotIndex} path`}
          />
        </div>
      )}
      {lease ? (
        <>
          <div>
            <strong>{lease.holderLabel || "Active holder"}</strong>{" "}
            <span className="badge slot-pill">
              {providerLabel(lease.provider)}
            </span>
            {lease.mode === "exclusive" && (
              <>
                {" "}
                <span className="badge slot-pill slot-full">Exclusive</span>
              </>
            )}
          </div>
          {lease.branch && (
            <small>
              Branch: <code>{lease.branch}</code>
            </small>
          )}
          {ttl && <small>TTL: {ttl}</small>}
          {lease.rescueRef && (
            <small>
              Rescue ref: <code>{lease.rescueRef}</code>
            </small>
          )}
          {lease.reason && <small>{lease.reason}</small>}
          {!repo.archived && (
            <div className="toolbar">
              {lease.state === "expired_dirty" ? (
                <button
                  type="button"
                  className="primary"
                  disabled={action.isPending}
                  onClick={() =>
                    action.mutate(() =>
                      commands.rescueLease(lease.id, {
                        revision: lease.revision,
                        reason: "Rescued from Worklog UI",
                      }),
                    )
                  }
                >
                  Rescue &amp; release
                </button>
              ) : (
                <button
                  type="button"
                  className="danger"
                  disabled={action.isPending}
                  onClick={() =>
                    action.mutate(() =>
                      commands.releaseLease(lease.id, {
                        revision: lease.revision,
                        force: true,
                        reason: "Force-released from Worklog UI",
                      }),
                    )
                  }
                >
                  Force release
                </button>
              )}
            </div>
          )}
        </>
      ) : (
        <p className="muted">Available for the next agent or dispatch.</p>
      )}
    </article>
  );
}

export function RepositorySlotsSection({ repo }: { repo: Repository }) {
  const slotsQuery = useData<RepositorySlots>(
    `/repositories/${repo.id}/slots`,
    5_000,
  );
  const dispatchQuery = useData<DispatchQueue>(
    `/dispatch-queue?repositoryId=${encodeURIComponent(repo.id)}&limit=50`,
    5_000,
  );
  const action = useWrites();

  // Guard against legacy test mocks that return `{ items: [], total: 0 }` for unknown GET paths.
  const data = slotsQuery.data?.policy ? slotsQuery.data : null;
  if (!data) {
    return slotsQuery.error ? <ErrorMessage error={slotsQuery.error} /> : null;
  }

  const { policy } = data;
  const dispatches = Array.isArray(dispatchQuery.data?.items)
    ? dispatchQuery.data.items
    : [];
  const dispatchByLease = new Map<string, DispatchItem>(
    dispatches.map((item) => [item.leaseId, item]),
  );
  const readyDispatches = dispatches.filter(
    (item) =>
      item.status === "provisioning" ||
      item.status === "preparing" ||
      item.status === "ready" ||
      item.status === "ready_manual",
  );
  const summary: RepositorySlotSummary = {
    maxActiveLeases: policy.maxActiveLeases,
    activeCount: data.activeCount,
    queuedCount: data.queuedCount,
    dirtyCount: data.dirtyCount,
    rescuedCount: data.recentLeases.filter((l) => Boolean(l.rescueRef)).length,
    baseReadonly: policy.baseReadonly,
    configured: policy.configured,
  };

  return (
    <section className="slots-section" aria-label="Checkout slots and queue">
      <div className="section-heading">
        <h2>
          Checkout slots &amp; queue <SlotSummaryPills summary={summary} />
        </h2>
      </div>
      <ErrorMessage error={action.error || slotsQuery.error} />
      <div className="slot-lock-bar">
        <div>
          <strong>Base checkout:</strong>{" "}
          {policy.baseCheckoutPath ? (
            <code>{policy.baseCheckoutPath}</code>
          ) : (
            <span className="muted">Not configured</span>
          )}{" "}
          <span
            className={`badge slot-pill${policy.baseReadonly ? " slot-locked" : ""}`}
          >
            {policy.baseReadonly ? "Read-only locked" : "Writable"}
          </span>
        </div>
        <button
          type="button"
          disabled={
            !policy.baseCheckoutPath || repo.archived || action.isPending
          }
          onClick={() =>
            action.mutate(() =>
              commands.setRepositoryBaseLock(repo.id, {
                locked: !policy.baseReadonly,
              }),
            )
          }
        >
          {policy.baseReadonly ? "Unlock base" : "Lock base read-only"}
        </button>
      </div>

      <SlotPolicyForm
        key={`${repo.id}:${policy.revision}`}
        repo={repo}
        policy={policy}
        action={action}
      />

      <h3>Slots</h3>
      {data.slots.length === 0 ? (
        <p className="muted">
          Frozen (0 active slots). Increase max active slots above to allow
          checkouts.
        </p>
      ) : (
        <div className="slot-cards">
          {data.slots.map((slot) => (
            <SlotCard
              key={slot.slotIndex}
              slot={slot}
              repo={repo}
              action={action}
            />
          ))}
        </div>
      )}

      <h3>Wait queue</h3>
      {data.queuedLeases.length === 0 ? (
        <p className="muted">No agents or sessions waiting in queue.</p>
      ) : (
        <ol className="slot-queue-list">
          {data.queuedLeases.map((lease) => {
            const dispatch = dispatchByLease.get(lease.id);
            return (
              <li className="slot-queue-row" key={lease.id}>
                <div>
                  <strong>#{lease.queuePosition ?? "?"}</strong>{" "}
                  <span>{lease.holderLabel || "Queued waiter"}</span>{" "}
                  <span className="badge slot-pill">
                    {providerLabel(lease.provider)}
                  </span>
                  {lease.priority !== "normal" && (
                    <>
                      {" "}
                      <span className="badge slot-pill slot-queued">
                        {lease.priority}
                      </span>
                    </>
                  )}
                  {lease.branch && (
                    <>
                      {" "}
                      <code>{lease.branch}</code>
                    </>
                  )}
                  {dispatch && (
                    <>
                      {" "}
                      ·{" "}
                      <a href={`#work/${dispatch.itemId}`}>
                        {dispatch.itemTitle}
                      </a>
                    </>
                  )}
                </div>
                {!repo.archived && (
                  <button
                    type="button"
                    className="quiet"
                    disabled={action.isPending}
                    onClick={() =>
                      action.mutate(() =>
                        dispatch
                          ? commands.cancelDispatch(dispatch.id, {
                              reason: "Cancelled from Worklog UI",
                            })
                          : commands.releaseLease(lease.id, {
                              revision: lease.revision,
                              force: true,
                              reason: "Cancelled from Worklog UI",
                            }),
                      )
                    }
                  >
                    Cancel
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {readyDispatches.length > 0 && (
        <>
          <h3>Promoted &amp; ready dispatches</h3>
          <ul className="slot-queue-list">
            {readyDispatches.map((dq) => (
              <li className="slot-queue-row" key={dq.id}>
                <div>
                  <a href={`#work/${dq.itemId}`}>{dq.itemTitle}</a>{" "}
                  <span className="badge slot-pill">
                    {providerLabel(dq.provider)}
                  </span>{" "}
                  <span className="badge slot-pill">{dq.status}</span>
                  {dq.slotIndex !== null && (
                    <>
                      {" "}
                      <small>Slot {dq.slotIndex}</small>
                    </>
                  )}
                  {dq.branch && (
                    <>
                      {" "}
                      <code>{dq.branch}</code>
                    </>
                  )}
                </div>
                <div className="toolbar">
                  {dq.status === "ready" && (
                    <button
                      type="button"
                      disabled={action.isPending}
                      onClick={() =>
                        action.mutate(() => commands.openDispatch(dq.id))
                      }
                    >
                      Open in app
                    </button>
                  )}
                  {dq.status === "ready_manual" && (
                    <a className="button" href={`#work/${dq.itemId}`}>
                      View slot brief
                    </a>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
