import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, contextStorageKey, getDataContext, request } from "./api";
import type { Schema } from "./apiTypes";
import { sessionStore } from "./storage";
import { ErrorMessage } from "./ui/Messages";

export type SessionLaunch = Schema["SessionLaunch"];
type SessionStart = Schema["SessionStart"];

type Provider = SessionStart["provider"];

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
    (candidate.provider === "codex" || candidate.provider === "claude") &&
    (candidate.status === "starting" ||
      candidate.status === "ready" ||
      candidate.status === "failed") &&
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
}: {
  itemId: string;
  title: string;
  description: string;
  archived: boolean;
}) {
  const client = useQueryClient();
  const [formOpen, setFormOpen] = useState(false);
  const [provider, setProvider] = useState<Provider>("codex");
  const [cwd, setCwd] = useState("");
  const [prompt, setPrompt] = useState(() =>
    description.trim() ? `${title}\n\n${description}` : title,
  );
  const [promptEdited, setPromptEdited] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [opening, setOpening] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);
  const [openError, setOpenError] = useState<unknown>(null);
  const [launch, setLaunch] = useState<SessionLaunch | null>(() =>
    storedLaunch(itemId),
  );
  const [expiredSaved, setExpiredSaved] = useState(() => storedExpired(itemId));
  const submissionInFlight = useRef(false);
  const openingInFlight = useRef(false);
  const lastRefresh = useRef("");

  const launchPath = launch
    ? `/session-launches/${launch.id}`
    : "/session-launches";
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
    enabled: !expiredSaved && launch?.status === "starting",
    refetchInterval: (query) =>
      !expiredSaved &&
      !query.state.error &&
      (query.state.data ?? launch)?.status === "starting"
        ? 1000
        : false,
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
    if (!current || (current.status !== "ready" && !current.chat)) return;
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
            path.startsWith("/chats/"))
        );
      },
    });
  }, [client, current?.chat?.id, current?.id, current?.status, itemId]);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (archived || submissionInFlight.current) return;
    const workingFolder = cwd.trim();
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
      const body: SessionStart = { provider, cwd: workingFolder, prompt };
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

  return (
    <section className="session-launch">
      <h3>Start a session</h3>
      {!current && !expired && (
        <p className="muted">
          Preparation sends your prompt to the selected provider.{" "}
          {provider === "codex"
            ? "Codex preparation is read-only; shell tools, web search, apps, and plugins are disabled. User config is skipped, but workspace context may still load."
            : "Claude tools are disabled during preparation."}{" "}
          Send Continue in the desktop app to begin work.
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
              </select>
            </label>
            <label htmlFor={`session-cwd-${itemId}`}>
              Working folder
              <input
                id={`session-cwd-${itemId}`}
                aria-label="Working folder"
                aria-required="true"
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
          </div>
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
              {current
                ? current.provider === "codex"
                  ? "Codex"
                  : "Claude"
                : "provider"}{" "}
              history first; the provider session may still exist.
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
              {current?.status === "starting" && (
                <p>Preparing a linked chat…</p>
              )}
              {current?.status === "ready" && (
                <p>
                  Chat prepared and linked. Open request sent. Send Continue in
                  the desktop app to begin work.
                </p>
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
          {current?.chat && (
            <p>
              Linked chat: <strong>{current.chat.title}</strong>
            </p>
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
          {!expired && current?.status === "starting" && status.error && (
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
          <ErrorMessage error={displayedError} />
        </div>
      )}
    </section>
  );
}
