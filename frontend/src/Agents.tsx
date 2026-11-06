import { useState, type ReactNode } from "react";
import { AgentTiedTo, AgentTies } from "./AgentTies";
import { useData } from "./hooks";
import { Empty, ErrorMessage } from "./components";
import { EndRun, Pages } from "./Coordination";
import { AgentProposals } from "./ProposalHistory";
import type { List } from "./types";
import type {
  AgentActivity,
  AgentDetail,
  AgentSummary,
  RegistryRole,
  RegistryWork,
  TieReason,
  Ties,
} from "./agentTypes";

const PAGE = 50;
const REFRESH = 30_000;
const reasons: Record<TieReason, string> = {
  participation: "Reported work",
  role: "Responsibility",
  update: "Status update",
  message: "Discussion",
  run: "Run",
  chat: "Tracked chat",
};
function timestamp(value: string) {
  return <time dateTime={value}>{new Date(value).toLocaleString()}</time>;
}
const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
// Reports are provenance: a fresh one says the agent spoke lately, not that it is running.
function state(agent: AgentSummary) {
  if (!agent.active) return "Credential revoked";
  if (agent.working.some((work) => !work.stale)) return "Reporting work";
  return agent.working.length ? "Stale reports" : "No current reports";
}
export function Agents({ selected }: { selected?: string }) {
  return selected ? <AgentView key={selected} id={selected} /> : <AgentList />;
}
function AgentList() {
  const [offset, setOffset] = useState(0);
  const list = useData<List<AgentSummary>>(
    `/agents?limit=${PAGE}&offset=${offset}`,
    REFRESH,
  );
  return (
    <>
      <header>
        <h1>Agents</h1>
        <p className="lede">
          Who works on what, from the runs, repository work, messages and
          responsibilities agents report.
        </p>
      </header>
      <ErrorMessage error={list.error} />
      {list.isPending ? (
        <p>Loading agents…</p>
      ) : !list.error && !list.data?.items.length ? (
        <Empty>
          No agents yet. Create an agent credential in Settings; each agent
          appears here with what it reports.
        </Empty>
      ) : null}
      <div className="agent-list">
        {(list.data?.items ?? []).map((agent) => (
          <article className="agent-row" key={agent.id}>
            <div className="agent-row-head">
              <a className="item-title" href={"#agents/" + agent.id}>
                {agent.label}
              </a>
              <span className="badge">{state(agent)}</span>
            </div>
            <AgentFacts agent={agent} />
            <WorkingOn work={agent.working} />
            <Roles roles={agent.roles} />
          </article>
        ))}
      </div>
      <Pages
        offset={offset}
        total={list.data?.total ?? 0}
        change={setOffset}
        label="agents"
      />
    </>
  );
}
function AgentFacts({ agent }: { agent: AgentSummary }) {
  return (
    <p className="muted">
      {agent.providers.join(", ") || "Provider unspecified"} · {agent.openRuns}{" "}
      open of {count(agent.runCount, "run")} ·{" "}
      {count(agent.messageCount, "message")} ·{" "}
      {agent.lastActivityAt ? (
        <>Last active {timestamp(agent.lastActivityAt)}</>
      ) : (
        "No activity yet"
      )}
    </p>
  );
}
function WorkingOn({ work }: { work: RegistryWork[] }) {
  return work.length ? (
    <ul className="agent-work" aria-label="Reported work">
      {work.map((w) => (
        <li key={w.participationId}>
          {w.item ? (
            <>
              <span className="muted">Working on</span>
              <a href={"#work/" + w.item.id}>{w.item.title}</a>
            </>
          ) : (
            <span className="muted">Working in</span>
          )}
          {w.roadmap && (
            <a className="person-badge" href={"#roadmaps/" + w.roadmap.id}>
              {w.roadmap.title}
            </a>
          )}
          {w.contexts.map((context, i) => (
            <a
              className="person-badge"
              key={i}
              href={"#repositories/" + context.repositoryId}
            >
              {context.repositoryName || context.repositoryId}
              {context.branch ? " · " + context.branch : ""}
            </a>
          ))}
          <small>
            {w.runName} run · {w.stale ? "Stale report · " : ""}Last reported{" "}
            {timestamp(w.lastSeenAt)}
          </small>
          {w.description && <p className="preserve-lines">{w.description}</p>}
        </li>
      ))}
    </ul>
  ) : null;
}
function roleHref(role: RegistryRole) {
  if (role.contextType === "item") return "#work/" + role.contextId;
  if (role.contextType === "roadmap") return "#roadmaps/" + role.contextId;
  return role.roadmap
    ? `#roadmaps/${role.roadmap.id}/${role.contextId}`
    : "#roadmaps";
}
function Roles({ roles }: { roles: RegistryRole[] }) {
  return roles.length ? (
    <ul className="agent-work" aria-label="Responsibilities">
      {roles.map((role) => (
        <li key={role.id}>
          <span className="muted">{role.label} for</span>
          <a href={roleHref(role)}>{role.contextTitle}</a>
          {role.roadmap && role.contextType !== "roadmap" && (
            <a className="person-badge" href={"#roadmaps/" + role.roadmap.id}>
              {role.roadmap.title}
            </a>
          )}
        </li>
      ))}
    </ul>
  ) : null;
}
function TieList<T extends { id: string; archived: boolean }>({
  title,
  ties,
  empty,
  children,
}: {
  title: string;
  ties: Ties<T>;
  empty: string;
  children: (tie: Ties<T>["items"][number]) => ReactNode;
}) {
  return (
    <section className="agent-ties" aria-label={title}>
      <h3>{title}</h3>
      {!ties.items.length && <p className="muted">{empty}</p>}
      {ties.items.map((tie) => (
        <div className="line" key={tie.id}>
          <span className="agent-tie">
            {children(tie)}
            {tie.archived && <small> (archived)</small>}
          </span>
          <span className="person-badges">
            {tie.current && <span className="badge">Current</span>}
            {tie.via.map((via) => (
              <span className="badge" key={via}>
                {reasons[via]}
              </span>
            ))}
          </span>
          {tie.lastAt && <small>{timestamp(tie.lastAt)}</small>}
        </div>
      ))}
      {ties.total > ties.items.length && (
        <p className="muted">
          Showing {ties.items.length} of {ties.total}.
        </p>
      )}
    </section>
  );
}
function AgentView({ id }: { id: string }) {
  const query = useData<AgentDetail>(
    "/agents/" + encodeURIComponent(id),
    REFRESH,
  );
  const agent = query.data;
  if (!agent)
    return (
      <>
        <ErrorMessage error={query.error} />
        {query.isPending && <p>Loading agent…</p>}
      </>
    );
  return (
    <>
      <a className="back" href="#agents">
        All agents
      </a>
      <header className="title-row">
        <h1>{agent.label}</h1>
        <span className="badge">{state(agent)}</span>
      </header>
      <AgentFacts agent={agent} />
      <p className="muted">
        Apart from its ties, everything here comes from what this agent
        reported. Reports are not proof that a process is alive and become stale
        after two hours.
      </p>
      <section>
        <h2>Working now</h2>
        {agent.working.length ? (
          <WorkingOn work={agent.working} />
        ) : (
          <Empty>No current participation reported.</Empty>
        )}
      </section>
      <section>
        <h2>Responsibilities</h2>
        {agent.roles.length ? (
          <Roles roles={agent.roles} />
        ) : (
          <p className="muted">No responsibility roles owned.</p>
        )}
      </section>
      <AgentTiedTo agentId={id} />
      <AgentTies
        targetType="agent"
        targetId={id}
        heading="Agents tied to this agent"
        headingLevel={2}
      />
      <section>
        <h2>From its reports</h2>
        <p className="muted">
          The work its runs, participation, messages, roles and tracked chats
          tie it to.
        </p>
        <TieList
          title="Work items"
          ties={agent.workItems}
          empty="No work items."
        >
          {(item) => (
            <>
              <a href={"#work/" + item.id}>{item.title}</a>{" "}
              <small>{item.status}</small>
              {item.roadmap && (
                <a
                  className="person-badge"
                  href={"#roadmaps/" + item.roadmap.id}
                >
                  {item.roadmap.title}
                </a>
              )}
            </>
          )}
        </TieList>
        <TieList title="Roadmaps" ties={agent.roadmaps} empty="No roadmaps.">
          {(roadmap) => <a href={"#roadmaps/" + roadmap.id}>{roadmap.title}</a>}
        </TieList>
        <TieList
          title="Repositories"
          ties={agent.repositories}
          empty="No repositories."
        >
          {(repository) => (
            <a href={"#repositories/" + repository.id}>{repository.name}</a>
          )}
        </TieList>
        <TieList
          title="Tracked chats"
          ties={agent.chats}
          empty="No tracked chat carries a chat ID this agent reported."
        >
          {(chat) => (
            <>
              <a href={"#chats/" + chat.id}>{chat.title}</a>{" "}
              <small>
                {[chat.provider, chat.model].filter(Boolean).join(" · ") ||
                  "Provider unspecified"}{" "}
                · {chat.ownerTitle}
              </small>
            </>
          )}
        </TieList>
      </section>
      <section>
        <h2>Runs</h2>
        {!agent.runs.items.length && <p className="muted">No runs started.</p>}
        {agent.runs.items.map((run) => (
          <article className="line" key={run.id}>
            <strong>{run.displayName}</strong>
            <small>
              {run.provider || "Provider unspecified"}
              {run.createdAt && <> · Started {timestamp(run.createdAt)}</>}
              {run.endReason && <> · {run.endReason.replaceAll("_", " ")}</>}
            </small>
            {run.chats.map((chat) => (
              <a
                className="person-badge"
                key={chat.id}
                href={"#chats/" + chat.id}
              >
                {chat.title}
              </a>
            ))}
            {run.externalChatId && !run.chats.length && (
              <code>{run.externalChatId}</code>
            )}
            <EndRun run={run} />
          </article>
        ))}
        {agent.runs.total > agent.runs.items.length && (
          <p className="muted">
            Showing the latest {agent.runs.items.length} of {agent.runs.total}{" "}
            runs.
          </p>
        )}
      </section>
      <Activity id={id} />
      <AgentProposals id={id} />
      <section>
        <h2>Credentials</h2>
        {agent.credentials.map((credential) => (
          <div className="line" key={credential.id}>
            <strong>{credential.label}</strong>
            <small>
              {credential.registeredBy ? (
                <>
                  Registered by{" "}
                  <a href={"#agents/" + credential.registeredBy.id}>
                    {credential.registeredBy.label}
                  </a>
                </>
              ) : (
                "Issued"
              )}{" "}
              {credential.chatId && (
                <>
                  for chat <code>{credential.chatId}</code>{" "}
                </>
              )}
              {timestamp(credential.createdAt)} ·{" "}
              {credential.revoked ? "Revoked" : "Active"}
            </small>
          </div>
        ))}
        <p className="muted">
          Issue, rotate and revoke credentials in{" "}
          <a href="#settings">Settings</a>.
        </p>
      </section>
    </>
  );
}
function Activity({ id }: { id: string }) {
  const [offset, setOffset] = useState(0);
  const query = useData<List<AgentActivity>>(
    `/agents/${encodeURIComponent(id)}/activity?limit=${PAGE}&offset=${offset}`,
    REFRESH,
  );
  return (
    <section aria-label="Messages and updates">
      <h2>Messages and updates</h2>
      <ErrorMessage error={query.error} />
      {query.isPending ? (
        <p>Loading activity…</p>
      ) : !query.error && !query.data?.items.length ? (
        <p className="muted">No messages or status updates yet.</p>
      ) : null}
      {(query.data?.items ?? []).map((activity) => (
        <article
          className="line agent-activity"
          key={activity.kind + activity.id}
        >
          {activity.kind === "message" ? (
            <p>
              <strong>Message</strong> in{" "}
              <a href={"#discussions/" + activity.discussionId}>
                {activity.discussionTitle}
              </a>{" "}
              · {timestamp(activity.createdAt)}
              {activity.chats.map((chat) => (
                <a
                  className="person-badge"
                  key={chat.id}
                  href={"#chats/" + chat.id}
                >
                  {chat.title}
                </a>
              ))}
            </p>
          ) : (
            <p>
              <strong>
                {activity.updateKind === "blocker"
                  ? "Blocker"
                  : activity.updateKind === "handoff"
                    ? "Handoff"
                    : "Update"}
              </strong>{" "}
              · {activity.runName} · {timestamp(activity.createdAt)}
              {activity.item && (
                <>
                  {" "}
                  ·{" "}
                  <a href={"#work/" + activity.item.id}>
                    {activity.item.title ?? "Work item"}
                  </a>
                </>
              )}
              {activity.contexts.map((context, i) => (
                <a
                  className="person-badge"
                  key={i}
                  href={"#repositories/" + context.repositoryId}
                >
                  {context.repositoryName || context.repositoryId}
                </a>
              ))}
            </p>
          )}
          <p className="preserve-lines">
            {activity.text}
            {activity.truncated ? "…" : ""}
          </p>
        </article>
      ))}
      <Pages
        offset={offset}
        total={query.data?.total ?? 0}
        change={setOffset}
        label="activity"
      />
    </section>
  );
}
