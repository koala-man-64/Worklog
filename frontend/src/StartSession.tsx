import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, contextStorageKey, getDataContext, request } from "./api";
import type { RepositorySlots, Schema } from "./apiTypes";
import { commands } from "./commands";
import { sessionStore } from "./storage";
import { CopyButton } from "./ui/CopyButton";
import { ErrorMessage } from "./ui/Messages";

export type SessionLaunch = Schema["SessionLaunch"];
type SessionStart = Schema["SessionStart"];
type SessionStartBody = Pick<SessionStart, "provider" | "prompt"> &
  Partial<SessionStart>;

type Provider = SessionStart["provider"];

const POLLING_STATUSES = new Set<SessionLaunch["status"]>([
  "starting",
  "queued",
  "provisioning",
  "preparing",
]);

const VALID_PROVIDERS = new Set(["codex", "claude", "antigravity"]);
const VALID_STATUSES = new Set([
  "queued",
  "provisioning",
  "starting",
  "preparing",
  "ready",
  "ready_manual",
  "failed",
  "cancelled",
]);

function isLocalWindowsPath(path: string) {
  return /^[a-zA-Z]:[\\/]/.test(path);
}

function errorMessage(message: string) {
  return new Error(message);
}

function isSessionLaunch(value: unknown): value is SessionLaunch {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<SessionLaunch>;
  return (
    typeof candidate.id === "string" &&
    typeof candidate.provider === "string" &&
    VALID_PROVIDERS.has(candidate.provider) &&
    typeof candidate.status === "string" &&
    VALID_STATUSES.has(candidate.status) &&
    typeof candidate.message === "string" &&
    (candidate.sessionId === undefined ||
      candidate.sessionId === null ||
      typeof candidate.sessionId === "string") &&
    (candidate.chat === null ||
      (!!candidate.chat &&
        typeof candidate.chat.id === "string" &&
        typeof candidate.chat.title === "string"))
  );
}

function providerSessionId(launch: SessionLaunch) {
  const sessionId = launch.sessionId?.trim();
  return sessionId || null;
}

function providerTitle(provider: Provider) {
  if (provider === "codex") return "Codex";
  if (provider === "claude") return "Claude";
  return "Antigravity";
}

function storedLaunch(itemId: string): SessionLaunch | null {
  const key = contextStorageKey("session-launch", itemId);
  const raw = sessionStore.read(key).value;
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (isSessionLaunch(parsed)) return parsed;
  } catch {
    // A malformed browser draft must not prevent opening the work item.
  }
  sessionStore.remove(key);
  return null;
}

function saveLaunch(itemId: string, launch: SessionLaunch) {
  sessionStore.write(
    contextStorageKey("session-launch", itemId),
    JSON.stringify(launch),
  );
}

function clearLaunch(itemId: string) {
  sessionStore.remove(contextStorageKey("session-launch", itemId));
}

function expiredLaunchKey(itemId: string) {
  return contextStorageKey("session-launch-expired", itemId);
}

function storedExpired(itemId: string) {
  return sessionStore.read(expiredLaunchKey(itemId)).value === "true";
}

function markExpired(itemId: string) {
  sessionStore.write(expiredLaunchKey(itemId), "true");
}

function clearExpired(itemId: string) {
  sessionStore.remove(expiredLaunchKey(itemId));
}

function isExpiredLaunchError(error: unknown) {
  return error instanceof ApiError && error.status === 404;
}

export function StartSession({
  itemId,
  title,
  description,
  archived,
  repositoryIds = [],
}: {
  itemId: string;
  title: string;
  description: string;
  archived: boolean;
  repositoryIds?: string[];
}) {
  const client = useQueryClient();
  const briefId = useId();
  const [formOpen, setFormOpen] = useState(false);
  const [provider, setProvider] = useState<Provider>("codex");
  const [selectedRepoId, setSelectedRepoId] = useState(
    () => repositoryIds[0] ?? "",
  );
  const [branch, setBranch] = useState("");
  const [cwd, setCwd] = useState("");
  const [prompt, setPrompt] = useState(() =>
    description.trim() ? `${title}\n\n${description}` : title,
  );
  const [promptEdited, setPromptEdited] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [opening, setOpening] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);
  const [openError, setOpenError] = useState<unknown>(null);
  const [launch, setLaunch] = useState<SessionLaunch | null>(() =>
    storedLaunch(itemId),
  );
  const [expiredSaved, setExpiredSaved] = useState(() => storedExpired(itemId));
  const submissionInFlight = useRef(false);
  const openingInFlight = useRef(false);
  const lastRefresh = useRef("");

  const linkedRepoIds = repositoryIds;
  const repoSlotsQueries = useQueries({
    queries: linkedRepoIds.map((rid) => ({
      queryKey: [getDataContext(), `/repositories/${rid}/slots`],
      queryFn: () => request<RepositorySlots>(`/repositories/${rid}/slots`),
      enabled: formOpen && linkedRepoIds.length > 0,
    })),
  });
  const slotsByRepo = new Map<string, RepositorySlots>();
  linkedRepoIds.forEach((rid, idx) => {
    const data = repoSlotsQueries[idx]?.data;
    if (data?.policy) slotsByRepo.set(rid, data);
  });
  const effectiveRepoId =
    selectedRepoId && linkedRepoIds.includes(selectedRepoId)
      ? selectedRepoId
      : "";
  const selectedSlots = effectiveRepoId
    ? slotsByRepo.get(effectiveRepoId) ?? null
    : null;
  const needsManualFolder =
    !effectiveRepoId || (!!selectedSlots && !selectedSlots.policy.baseCheckoutPath);

  const launchPath = launch
    ? `/session-launches/${launch.id}`
    : "/session-launches";
  const isPolling = !!launch && POLLING_STATUSES.has(launch.status);
  const status = useQuery<SessionLaunch>({
    queryKey: [getDataContext(), launchPath],
    queryFn: async () => {
      try {
        const latest = await request<SessionLaunch>(launchPath);
        saveLaunch(itemId, latest);
        return latest;
      } catch (error) {
        if (isExpiredLaunchError(error)) {
          markExpired(itemId);
          setExpiredSaved(true);
        }
        throw error;
      }
    },
    enabled: !expiredSaved && isPolling,
    refetchInterval: (query) => {
      const latest = query.state.data ?? launch;
      return !expiredSaved &&
        !query.state.error &&
        !!latest &&
        POLLING_STATUSES.has(latest.status)
        ? 1000
        : false;
    },
  });
  const current = status.data ?? launch;
  const expired =
    expiredSaved ||
    isExpiredLaunchError(status.error) ||
    isExpiredLaunchError(openError);
  const displayedError = expired ? null : status.error || openError;

  useEffect(() => {
    if (promptEdited) return;
    setPrompt(description.trim() ? `${title}\n\n${description}` : title);
  }, [description, promptEdited, title]);

  useEffect(() => {
    if (
      !current ||
      (current.status !== "ready" &&
        current.status !== "ready_manual" &&
        !current.chat)
    )
      return;
    const signature = `${current.id}:${current.status}:${current.chat?.id ?? ""}`;
    if (lastRefresh.current === signature) return;
    lastRefresh.current = signature;
    const context = getDataContext();
    void client.invalidateQueries({
      predicate: (query) => {
        const [queryContext, path] = query.queryKey;
        return (
          queryContext === context &&
          typeof path === "string" &&
          (path === `/work-items/${itemId}` ||
            path === "/chats" ||
            path.startsWith("/chats?") ||
            path.startsWith("/chats/") ||
            path.startsWith("/repositories/") ||
            path.startsWith("/dispatch-queue"))
        );
      },
    });
  }, [client, current?.chat?.id, current?.id, current?.status, itemId]);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (archived || submissionInFlight.current) return;
    const workingFolder = cwd.trim();
    if (!effectiveRepoId) {
      if (provider === "antigravity") {
        setFormError(
          errorMessage(
            "Link a repository to this work item to reserve an Antigravity checkout slot.",
          ),
        );
        return;
      }
      if (!workingFolder) {
        setFormError(errorMessage("Enter a working folder path."));
        return;
      }
      if (!isLocalWindowsPath(workingFolder)) {
        setFormError(
          errorMessage(
            "Enter an absolute local Windows path, such as C:\\Projects\\repo.",
          ),
        );
        return;
      }
    } else if (workingFolder && !isLocalWindowsPath(workingFolder)) {
      setFormError(
        errorMessage(
          "Enter an absolute local Windows path, such as C:\\Projects\\repo.",
        ),
      );
      return;
    }
    if (!prompt.trim()) {
      setFormError(errorMessage("Enter a task prompt."));
      return;
    }
    if (prompt.length > 12000) {
      setFormError(
        errorMessage("The task prompt must be 12,000 characters or fewer."),
      );
      return;
    }

    submissionInFlight.current = true;
    setSubmitting(true);
    setFormError(null);
    try {
      const body: SessionStartBody = effectiveRepoId
        ? {
            provider,
            repositoryId: effectiveRepoId,
            prompt,
            ...(branch.trim() ? { branch: branch.trim() } : {}),
            ...(workingFolder ? { cwd: workingFolder } : {}),
          }
        : { provider, cwd: workingFolder, prompt };
      const next = await request<SessionLaunch>(
        `/work-items/${itemId}/sessions`,
        "POST",
        body,
      );
      clearExpired(itemId);
      setExpiredSaved(false);
      saveLaunch(itemId, next);
      setLaunch(next);
      setFormOpen(false);
    } catch (error) {
      setFormError(error);
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }

  async function openLinkedChat() {
    if (!current || openingInFlight.current) return;
    openingInFlight.current = true;
    setOpening(true);
    setOpenError(null);
    try {
      const next = await request<SessionLaunch>(
        `/session-launches/${current.id}/open`,
        "POST",
      );
      saveLaunch(itemId, next);
      client.setQueryData(
        [getDataContext(), `/session-launches/${current.id}`],
        next,
      );
      setLaunch(next);
    } catch (error) {
      if (isExpiredLaunchError(error)) {
        markExpired(itemId);
        setExpiredSaved(true);
      }
      setOpenError(error);
    } finally {
      openingInFlight.current = false;
      setOpening(false);
    }
  }

  async function cancelQueued() {
    if (!current || cancelling) return;
    setCancelling(true);
    setOpenError(null);
    try {
      const dispatchId = current.dispatchId ?? current.id;
      await commands.cancelDispatch(dispatchId, {
        reason: "Cancelled from Worklog UI",
      });
      const updated: SessionLaunch = {
        ...current,
        status: "cancelled",
        queuePosition: null,
        message: "Queued dispatch cancelled.",
      };
      saveLaunch(itemId, updated);
      client.setQueryData(
        [getDataContext(), `/session-launches/${current.id}`],
        updated,
      );
      setLaunch(updated);
    } catch (error) {
      setOpenError(error);
    } finally {
      setCancelling(false);
    }
  }

  function availabilityLine(slots: RepositorySlots) {
    if (slots.policy.maxActiveLeases === 0) {
      return "Frozen (0/0) — session will queue until slots are unfrozen.";
    }
    if (slots.availableSlots > 0) {
      return `${slots.availableSlots}/${slots.policy.maxActiveLeases} slots free — will assign next open slot.`;
    }
    return `${slots.activeCount}/${slots.policy.maxActiveLeases} slots full — will queue at #${slots.queuedCount + 1}.`;
  }

  return (
    <section className="session-launch">
      <h3>Start a session</h3>
      {!current && !expired && (
        <p className="muted">
          {provider === "antigravity" ? (
            "Antigravity reserves a repository checkout slot and generates a ready-to-paste slot brief for your Antigravity conversation."
          ) : (
            <>
              Preparation sends your prompt to the selected provider.{" "}
              {provider === "codex"
                ? "Codex preparation is read-only; shell tools, web search, apps, and plugins are disabled. User config is skipped, but workspace context may still load. Trusted project settings may supply other tools."
                : "Claude tools are disabled during preparation."}{" "}
              Send Continue in the desktop app to begin work.
            </>
          )}
        </p>
      )}
      {!formOpen && !current && !expired && (
        <button
          type="button"
          className="primary"
          disabled={archived}
          onClick={() => {
            setFormError(null);
            setFormOpen(true);
          }}
        >
          Start session
        </button>
      )}
      {formOpen && !current && !expired && (
        <form className="session-launch-form" onSubmit={submit}>
          <div className="session-launch-fields">
            <label htmlFor={`session-provider-${itemId}`}>
              Provider
              <select
                id={`session-provider-${itemId}`}
                aria-label="Provider"
                value={provider}
                disabled={archived || submitting}
                onChange={(e) => setProvider(e.target.value as Provider)}
              >
                <option value="codex">Codex</option>
                <option value="claude">Claude</option>
                <option value="antigravity">Antigravity</option>
              </select>
            </label>
            {linkedRepoIds.length > 0 && (
              <label htmlFor={`session-repo-${itemId}`}>
                Repository
                <select
                  id={`session-repo-${itemId}`}
                  aria-label="Repository"
                  value={effectiveRepoId}
                  disabled={archived || submitting}
                  onChange={(e) => {
                    setSelectedRepoId(e.target.value);
                    setFormError(null);
                  }}
                >
                  {linkedRepoIds.map((rid) => (
                    <option key={rid} value={rid}>
                      {slotsByRepo.get(rid)?.repositoryName ?? rid}
                    </option>
                  ))}
                  <option value="">Custom folder (no slot)</option>
                </select>
              </label>
            )}
            {effectiveRepoId && (
              <label htmlFor={`session-branch-${itemId}`}>
                Branch (optional)
                <input
                  id={`session-branch-${itemId}`}
                  aria-label="Branch"
                  value={branch}
                  onChange={(e) => {
                    setBranch(e.target.value);
                    setFormError(null);
                  }}
                  disabled={archived || submitting}
                  placeholder="e.g. codex/task-name"
                  maxLength={200}
                  autoComplete="off"
                />
              </label>
            )}
            {needsManualFolder && (
              <label htmlFor={`session-cwd-${itemId}`}>
                Working folder
                <input
                  id={`session-cwd-${itemId}`}
                  aria-label="Working folder"
                  aria-required={!effectiveRepoId ? "true" : undefined}
                  aria-invalid={
                    formError instanceof Error && /path/i.test(formError.message)
                      ? true
                      : undefined
                  }
                  value={cwd}
                  onChange={(e) => {
                    setCwd(e.target.value);
                    setFormError(null);
                  }}
                  disabled={archived || submitting}
                  placeholder="C:\\Projects\\my-repo"
                  maxLength={2000}
                  autoComplete="off"
                />
              </label>
            )}
          </div>
          {selectedSlots && (
            <p className="session-launch-availability">
              {availabilityLine(selectedSlots)}
            </p>
          )}
          <label
            className="session-launch-prompt"
            htmlFor={`session-prompt-${itemId}`}
          >
            Task prompt
            <textarea
              id={`session-prompt-${itemId}`}
              aria-label="Task prompt"
              aria-required="true"
              aria-invalid={
                formError instanceof Error &&
                formError.message === "Enter a task prompt."
                  ? true
                  : undefined
              }
              rows={8}
              maxLength={12000}
              value={prompt}
              onChange={(e) => {
                setPrompt(e.target.value);
                setPromptEdited(true);
                setFormError(null);
              }}
              disabled={archived || submitting}
            />
          </label>
          <div className="toolbar">
            <button
              type="button"
              className="quiet"
              disabled={submitting}
              onClick={() => {
                setFormOpen(false);
                setFormError(null);
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="primary"
              disabled={archived || submitting}
            >
              {submitting ? "Preparing…" : "Start"}
            </button>
          </div>
          <ErrorMessage error={formError} />
        </form>
      )}
      {(current || expired) && (
        <div className="session-launch-status" aria-live="polite">
          {expired ? (
            <p>
              This saved launch receipt is no longer available. Check{" "}
              {current ? providerTitle(current.provider) : "provider"} history
              first; the provider session may still exist.
              {current?.sessionId && (
                <>
                  {" "}
                  Provider session ID: <code>{current.sessionId}</code>.
                </>
              )}{" "}
              Clearing this saved launch will not remove any tracked Worklog
              chat.
            </p>
          ) : (
            <>
              {current?.status === "queued" && (
                <>
                  <p>
                    Queued for{" "}
                    <strong>{current.repositoryName || "repository"}</strong> at
                    position <strong>#{current.queuePosition ?? 1}</strong>.
                  </p>
                  {!!current.activeHolders?.length && (
                    <p className="muted">
                      Active slots:{" "}
                      {current.activeHolders
                        .map(
                          (h) =>
                            `slot-${h.slotIndex ?? "?"} (${h.holderLabel}${h.branch ? ` · ${h.branch}` : ""})`,
                        )
                        .join(", ")}
                    </p>
                  )}
                </>
              )}
              {(current?.status === "provisioning" ||
                current?.status === "preparing") && (
                <p>{current.message || "Provisioning checkout slot…"}</p>
              )}
              {current?.status === "starting" && (
                <p>Preparing a linked chat…</p>
              )}
              {current?.status === "ready" && (
                <p>
                  {current.dispatchId && current.message
                    ? current.message
                    : "Chat prepared and linked. Open request sent. Send Continue in the desktop app to begin work."}
                </p>
              )}
              {current?.status === "ready_manual" && (
                <>
                  <p>
                    Checkout slot reserved for Antigravity. Copy the brief below
                    and paste it into an Antigravity conversation.
                  </p>
                  {current.copyablePrompt && (
                    <div className="session-launch-brief">
                      <textarea
                        id={briefId}
                        aria-label="Antigravity slot brief"
                        readOnly
                        rows={9}
                        value={current.copyablePrompt}
                      />
                      <div>
                        <CopyButton
                          targetId={briefId}
                          label="Copy slot brief"
                          className="primary"
                        />
                      </div>
                    </div>
                  )}
                </>
              )}
              {current?.status === "cancelled" && (
                <p>{current.message || "Queued dispatch cancelled."}</p>
              )}
              {current?.status === "failed" && (
                <p>{current.message || "The session could not be prepared."}</p>
              )}
              {current?.status === "failed" &&
                !current.chat &&
                providerSessionId(current) && (
                  <p>
                    Provider session exists; the Worklog chat link is pending.
                    ID: <code>{providerSessionId(current)}</code>
                  </p>
                )}
            </>
          )}
          {!expired && current?.slotIndex != null && (
            <p className="muted">
              Slot #{current.slotIndex}
              {current.branch && (
                <>
                  {" "}
                  · <code>{current.branch}</code>
                </>
              )}
              {current.worktreePath && (
                <>
                  {" "}
                  · <code>{current.worktreePath}</code>
                </>
              )}
            </p>
          )}
          {current?.chat && (
            <p>
              Linked chat: <strong>{current.chat.title}</strong>
            </p>
          )}
          <div className="toolbar">
            {!expired && current?.status === "queued" && (
              <button
                type="button"
                className="danger"
                disabled={cancelling}
                onClick={() => void cancelQueued()}
              >
                {cancelling ? "Cancelling…" : "Cancel queued dispatch"}
              </button>
            )}
            {!expired && current?.chat && (
              <button
                type="button"
                disabled={opening}
                onClick={() => void openLinkedChat()}
              >
                {opening ? "Opening…" : "Open in app"}
              </button>
            )}
            {!expired &&
              current &&
              POLLING_STATUSES.has(current.status) &&
              status.error && (
                <button
                  type="button"
                  disabled={status.isFetching}
                  onClick={() => {
                    void status.refetch();
                  }}
                >
                  {status.isFetching ? "Checking…" : "Retry status check"}
                </button>
              )}
            {!expired &&
              current?.status === "failed" &&
              !current.chat &&
              providerSessionId(current) && (
                <button
                  type="button"
                  disabled={opening}
                  onClick={() => void openLinkedChat()}
                >
                  {opening ? "Retrying…" : "Retry linking and opening"}
                </button>
              )}
            {expired && (
              <button
                type="button"
                className="quiet"
                onClick={() => {
                  clearLaunch(itemId);
                  clearExpired(itemId);
                  setExpiredSaved(false);
                  setLaunch(null);
                  setOpenError(null);
                  setFormError(null);
                  setFormOpen(!archived);
                }}
              >
                Clear expired launch to prepare another
              </button>
            )}
            {!expired &&
              (current?.status === "ready" ||
                current?.status === "ready_manual" ||
                current?.status === "cancelled" ||
                (current?.status === "failed" &&
                  (current.chat || !providerSessionId(current)))) && (
                <button
                  type="button"
                  className="quiet"
                  disabled={archived}
                  onClick={() => {
                    clearLaunch(itemId);
                    setLaunch(null);
                    setOpenError(null);
                    setFormError(null);
                    setFormOpen(true);
                  }}
                >
                  Start another session
                </button>
              )}
          </div>
          <ErrorMessage error={displayedError} />
        </div>
      )}
    </section>
  );
}
