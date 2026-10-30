import { useState } from "react";
import { localStore } from "./storage";
import { useQueryClient } from "@tanstack/react-query";
import { download, getDataContext } from "./api";
import { useAction, useData } from "./hooks";
import { ErrorMessage, formatBytes } from "./components";
import type { Settings as SettingsData } from "./types";
export function Settings() {
  const client = useQueryClient();
  const query = useData<SettingsData>("/settings");
  const action = useAction();
  const [label, setLabel] = useState("");
  const [token, setToken] = useState("");
  const [credentialMessage, setCredentialMessage] = useState("");
  const [message, setMessage] = useState("");
  const data = query.data;
  if (!data)
    return (
      <>
        <ErrorMessage error={query.error} />
        <p>Loading settings…</p>
      </>
    );
  return (
    <>
      <header>
        <h1>Settings</h1>
        <p className="lede">
          Preferences, backups and agent access for this machine.
        </p>
      </header>
      <ErrorMessage error={action.error} />
      {localStore
        .keys()
        .some(
          (key) =>
            /^worklog:.*:(draft|roadmap-structure):/.test(key) &&
            !key.startsWith("worklog:" + getDataContext() + ":"),
        ) && (
        <p className="warning">
          Local drafts from another data folder or backup identity are retained
          in this browser. They are isolated from this worklog and will not be
          applied here.
        </p>
      )}
      <div className="two-column">
        <section>
          <h2>Preferences</h2>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const form = new FormData(e.currentTarget);
              await action
                .mutateAsync({
                  path: "/settings",
                  method: "PATCH",
                  body: { timezone: form.get("timezone") },
                })
                .then((value) => {
                  const settings = value as SettingsData;
                  client.setQueryData<{
                    csrfToken: string;
                    today: string;
                    timezone: string;
                  }>(["session"], (old) =>
                    old
                      ? {
                          ...old,
                          today: settings.today,
                          timezone: settings.timezone,
                        }
                      : old,
                  );
                })
                .catch(() => undefined);
            }}
          >
            <label>
              Day boundary / IANA timezone
              <input
                name="timezone"
                defaultValue={data.timezone}
                placeholder="America/Chicago"
                required
              />
            </label>
            <button>Save timezone</button>
          </form>
          <label>
            Appearance
            <select
              defaultValue={localStore.read("theme").value ?? "system"}
              onChange={(e) => {
                localStore.write("theme", e.target.value);
                document.documentElement.dataset.theme = e.target.value;
              }}
            >
              <option value="system">Follow system</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
          <small>
            Worklog {data.version}, local date {data.today}
          </small>
        </section>
        <section>
          <h2>Storage & portability</h2>
          <dl className="metrics">
            <dt>Database</dt>
            <dd>{formatBytes(data.storage.databaseBytes)}</dd>
            <dt>Backups</dt>
            <dd>{formatBytes(data.storage.backupBytes)}</dd>
          </dl>
          {data.storage.warning && (
            <p className="warning">{data.storage.warning}</p>
          )}
          <div className="toolbar">
            <button
              disabled={action.isPending}
              onClick={async () => {
                await action
                  .mutateAsync({ path: "/backups" })
                  .then(() => setMessage("Backup created."))
                  .catch(() => undefined);
              }}
            >
              Create backup
            </button>
            <a className="button" href={download("/export")}>
              Export everything (.zip)
            </a>
          </div>
          <p role="status">{message}</p>
          {data.backups.map((b) => (
            <div className="line" key={b.name}>
              <code>{b.name}</code>
              <small>{formatBytes(b.size)}</small>
              <small>{new Date(b.createdAt).toLocaleString()}</small>
            </div>
          ))}
          <p className="muted">
            Recovery is an offline operation. Stop Worklog and use the backup
            recovery command documented in the local README.
          </p>
        </section>
      </div>
      <section>
        <h2>Agent credentials</h2>
        <p>
          Agents can propose changes. Only your review can apply them. A new
          token is shown once; store it before dismissing.
        </p>
        <form
          className="inline"
          onSubmit={async (e) => {
            e.preventDefault();
            await action
              .mutateAsync({ path: "/credentials", body: { label } })
              .then((result) => {
                const issued = (result as { token: string | null }).token;
                if (typeof issued === "string") {
                  setToken(issued);
                  setCredentialMessage("");
                } else
                  setCredentialMessage(
                    "This credential was already issued and its token cannot be shown again. Revoke it and create a new credential if you did not save the token.",
                  );
                setLabel("");
              })
              .catch(() => undefined);
          }}
        >
          <input
            aria-label="Credential label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Agent name / purpose"
            required
          />
          <button disabled={action.isPending || !!token}>
            Create credential
          </button>
        </form>
        {credentialMessage && (
          <p className="warning" role="status">
            {credentialMessage}
          </p>
        )}
        {token && (
          <div className="token">
            <strong>Copy this token now. It will not be shown again.</strong>
            <code>{token}</code>
            <div className="toolbar">
              <button onClick={() => navigator.clipboard.writeText(token)}>
                Copy token
              </button>
              <button onClick={() => setToken("")}>I have saved it</button>
            </div>
          </div>
        )}
        {data.credentials.map((c) => (
          <div className="line" key={c.id}>
            <strong>{c.label}</strong>
            <small>{new Date(c.createdAt).toLocaleString()}</small>
            <small>{c.revoked ? "Revoked" : "Active"}</small>
            {!c.revoked && (
              <button
                onClick={() =>
                  action.mutate({ path: `/credentials/${c.id}/revoke` })
                }
              >
                Revoke
              </button>
            )}
          </div>
        ))}
      </section>
      <section>
        <h2>Archive & recovery</h2>
        <p>
          Archived work and roadmaps retain their history. Use the archive
          switches in Work and Roadmaps to recover them. Notes, tasks, files,
          and paths can be recovered inside their owning record.
        </p>
        <div className="toolbar">
          <a className="button" href="#work">
            Open work archive
          </a>
          <a className="button" href="#roadmaps">
            Open roadmap archive
          </a>
        </div>
      </section>
    </>
  );
}
