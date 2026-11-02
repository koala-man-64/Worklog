import { useState } from "react";
import { localStore } from "./storage";
import { useQueryClient } from "@tanstack/react-query";
import { download, getDataContext } from "./api";
import { useAction, useData } from "./hooks";
import {
  AVATAR_COLORS,
  AVATAR_EYES,
  AVATAR_TOPS,
  Avatar,
  avatarName,
  avatarParts,
  type AvatarParts,
} from "./Avatar";
import { ErrorMessage, formatBytes } from "./components";
import type { AvatarCatalog } from "./discussionTypes";
import type { Settings as SettingsData } from "./types";
export function Settings() {
  const client = useQueryClient();
  const query = useData<SettingsData>("/settings");
  const catalog = useData<AvatarCatalog>("/discussions/avatars");
  const action = useAction();
  const [label, setLabel] = useState("");
  // Null keeps the server's latest draw until the person picks one.
  const [avatar, setAvatar] = useState<string | null>(null);
  const chosenAvatar = avatar ?? catalog.data?.suggested;
  const avatarTaken = !!(
    catalog.data &&
    chosenAvatar &&
    !catalog.data.items.some(
      (entry) => entry.available && entry.name === chosenAvatar,
    )
  );
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
          Agents can propose changes, and discussion messages keep a stable
          agent identity across token rotation. A new token is shown once; store
          it before dismissing. An agent with a credential can also register a
          separate identity for itself with <code>worklog agent register</code>;
          its credential is listed here with the agent that registered it.
        </p>
        <form
          className="inline"
          onSubmit={async (e) => {
            e.preventDefault();
            await action
              .mutateAsync({
                path: "/credentials",
                body: chosenAvatar
                  ? { label, avatar: chosenAvatar }
                  : { label },
              })
              .then((result) => {
                const issued = (result as { token: string | null }).token;
                if (typeof issued === "string") {
                  setToken(issued);
                  const agentId = (result as { agentId?: string }).agentId;
                  const agent = data.agents.find(
                    (entry) => entry.id === agentId,
                  );
                  setCredentialMessage(
                    agent
                      ? `New credential for ${agent.label}.`
                      : "New credential created.",
                  );
                } else
                  setCredentialMessage(
                    "This credential was already issued and its token cannot be shown again. Revoke it and create a new credential if you did not save the token.",
                  );
                setLabel("");
                setAvatar(null);
              })
              .catch(() => undefined);
          }}
        >
          {catalog.data && chosenAvatar && (
            <AvatarPicker
              value={chosenAvatar}
              catalog={catalog.data}
              taken={avatarTaken}
              onChange={setAvatar}
            />
          )}
          <input
            aria-label="Credential label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Agent name / purpose"
            required
          />
          <button disabled={action.isPending || !!token || avatarTaken}>
            Create credential
          </button>
        </form>
        <ErrorMessage error={catalog.error} />
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
        {data.agents.length > 0 && (
          <div className="discussion-profile-list" aria-label="Agent profiles">
            <h3>Stable discussion profiles</h3>
            {data.agents.map((agent) => (
              <div className="line" key={agent.id}>
                <Avatar name={agent.avatar} />
                <strong>{agent.label}</strong>
                <small>Profile {agent.id}</small>
              </div>
            ))}
          </div>
        )}
        {data.credentials.map((c) => (
          <div className="line" key={c.id}>
            <span className="credential-identity">
              <strong>{c.label}</strong>
              <small>
                Discussion profile:{" "}
                {data.agents.find((agent) => agent.id === c.agentId)?.label ??
                  "Unavailable"}
              </small>
              {c.registeredBy && (
                <small>
                  Registered by{" "}
                  <a href={"#agents/" + c.registeredBy.id}>
                    {c.registeredBy.label}
                  </a>
                </small>
              )}
              {!c.revoked && c.registeredActive > 0 && (
                <small>
                  Registered {c.registeredActive} active agent
                  {c.registeredActive === 1 ? "" : "s"}; revoking this
                  credential revokes them too
                </small>
              )}
            </span>
            <small>{new Date(c.createdAt).toLocaleString()}</small>
            <small>{c.revoked ? "Revoked" : "Active"}</small>
            <button
              type="button"
              disabled={action.isPending || !!token}
              onClick={async () => {
                await action
                  .mutateAsync({
                    path: `/credentials/${c.id}/rotate`,
                    body: {},
                  })
                  .then((result) => {
                    const issued = (result as { token?: unknown }).token;
                    const profile = data.agents.find(
                      (agent) => agent.id === c.agentId,
                    );
                    if (typeof issued === "string") {
                      setToken(issued);
                      setCredentialMessage(
                        `Rotated ${c.label}; its discussion identity remains ${profile?.label ?? "the same profile"}. The old token was revoked.`,
                      );
                    } else {
                      setCredentialMessage(
                        "The credential was rotated, but its one-time token could not be recovered. Check the credential list for the replacement before rotating again.",
                      );
                    }
                  })
                  .catch(() => undefined);
              }}
            >
              {c.revoked ? "Replace token" : "Rotate token"}
            </button>
            {!c.revoked && (
              <button
                type="button"
                disabled={action.isPending || !!token}
                onClick={() =>
                  action.mutate({ path: `/credentials/${c.id}/revoke` })
                }
              >
                {c.registeredActive > 0
                  ? `Revoke with ${c.registeredActive} registered`
                  : "Revoke"}
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

const COLOR_NAMES = Object.keys(AVATAR_COLORS) as AvatarParts["color"][];

/** Shows the avatar a new agent will get; the parts change it, Shuffle draws a free one. */
function AvatarPicker({
  value,
  catalog,
  taken,
  onChange,
}: {
  value: string;
  catalog: AvatarCatalog;
  taken: boolean;
  onChange: (name: string) => void;
}) {
  const parts = avatarParts(value) ?? avatarParts(catalog.suggested);
  if (!parts) return null;
  const free = catalog.items.filter((entry) => entry.available);
  const part = <K extends keyof AvatarParts>(
    key: K,
    label: string,
    options: readonly AvatarParts[K][],
  ) => (
    <label>
      {label}
      <select
        value={parts[key]}
        onChange={(e) =>
          onChange(
            avatarName({ ...parts, [key]: e.target.value as AvatarParts[K] }),
          )
        }
      >
        {options.map((option) => (
          <option key={option}>{option}</option>
        ))}
      </select>
    </label>
  );
  return (
    <fieldset className="avatar-picker">
      <legend>Avatar</legend>
      <Avatar name={value} size={36} />
      {part("color", "Colour", COLOR_NAMES)}
      {part("eyes", "Eyes", AVATAR_EYES)}
      {part("top", "Top", AVATAR_TOPS)}
      <button
        type="button"
        disabled={!free.length}
        onClick={() =>
          onChange(free[Math.floor(Math.random() * free.length)].name)
        }
      >
        Shuffle
      </button>
      {taken && (
        <small className="warning" role="status">
          An active agent already shows this avatar. Choose another.
        </small>
      )}
    </fieldset>
  );
}
