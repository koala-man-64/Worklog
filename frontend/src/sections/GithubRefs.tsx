import { useState } from "react";
import { commands } from "../commands";
import { useWrites } from "../hooks";
import type { AgentChat, GithubRef } from "../apiTypes";
import { ErrorMessage } from "../ui/Messages";
export function GithubRefsSection({
  ownerType,
  ownerId,
  github = [],
  chats = [],
  disabled = false,
}: {
  ownerType: "item" | "roadmap";
  ownerId: string;
  github?: GithubRef[];
  chats?: AgentChat[];
  disabled?: boolean;
}) {
  const action = useWrites();
  const [kind, setKind] = useState<"pr" | "commit">("pr");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [reference, setReference] = useState("");
  const [description, setDescription] = useState("");
  const [chatId, setChatId] = useState("");
  const active = github.filter((g) => !g.archived);
  const activeChats = chats.filter((c) => !c.archived);
  return (
    <section>
      <h3>
        GitHub commits & PRs <span className="count">{active.length}</span>
      </h3>
      {active.map((g) => (
        <div className="line" key={g.id}>
          <span className="person-badge">
            {g.kind === "pr" ? "PR" : "Commit"}
          </span>
          <a href={g.url} target="_blank" rel="noopener noreferrer">
            {g.title || g.reference || g.url} ↗
          </a>
          {g.reference && <code>{g.reference}</code>}
          {g.chatTitle && <small>Chat: {g.chatTitle}</small>}
          {g.description && <small>{g.description}</small>}
          {!disabled && (
            <button
              type="button"
              className="quiet"
              aria-label={"Archive GitHub " + (g.title || g.reference || g.url)}
              onClick={() =>
                action.mutate(() =>
                  commands.setArchived("github", g.id, true, {
                    revision: g.revision,
                  }),
                )
              }
            >
              Archive
            </button>
          )}
        </div>
      ))}
      {!disabled && (
        <form
          className="inline"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!url.trim()) return;
            await action
              .mutateAsync(() =>
                commands.createGithubRef({
                  ownerType,
                  ownerId,
                  kind,
                  url: url.trim(),
                  title: title.trim(),
                  reference: reference.trim(),
                  description: description.trim(),
                  chatId: chatId || null,
                }),
              )
              .then(() => {
                setUrl("");
                setTitle("");
                setReference("");
                setDescription("");
                setChatId("");
              })
              .catch(() => undefined);
          }}
        >
          <select
            aria-label="GitHub reference type"
            value={kind}
            onChange={(e) => setKind(e.target.value as "pr" | "commit")}
          >
            <option value="pr">Pull request</option>
            <option value="commit">Commit</option>
          </select>
          <input
            aria-label="GitHub URL"
            placeholder="https://github.com/org/repo/pull/123 or /commit/sha…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
          />
          <input
            aria-label="GitHub title"
            placeholder="Title or summary…"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <input
            aria-label="GitHub SHA or PR number"
            placeholder="SHA or PR # (auto if blank)…"
            value={reference}
            onChange={(e) => setReference(e.target.value)}
          />
          <input
            aria-label="GitHub description"
            placeholder="Description (optional)…"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <select
            aria-label="Associated agent chat"
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
          >
            <option value="">No linked agent chat</option>
            {activeChats.map((c) => (
              <option key={c.id} value={c.id}>
                Chat: {c.title} ({c.chatId})
              </option>
            ))}
          </select>
          <button type="submit" disabled={action.isPending || !url.trim()}>
            Add GitHub ref
          </button>
        </form>
      )}
      <ErrorMessage error={action.error} />
    </section>
  );
}
