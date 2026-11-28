import { useState } from "react";
import { commands } from "./commands";
import { useData, useWrites } from "./hooks";
import { LinkPicker } from "./LinkPicker";
import type { Answer, Schema } from "./apiTypes";
import { Empty, ErrorMessage } from "./ui/Messages";

type EndpointType = Schema["DependencyRef"]["type"];
type EndpointOption = Schema["DependencyRef"] & { title: string };
type ContextType = Schema["WaitContextRef"]["type"];
type PickOption = {
  id: string;
  title?: string;
  name?: string;
  label?: string;
  active?: boolean;
};

const ENDPOINT_TYPES: EndpointType[] = ["item", "step", "ticket", "repository", "agent"];
const CONTEXT_TYPES: ContextType[] = ["item", "step", "ticket", "repository"];
const DEPENDENCY_REFRESH_MS = 30_000;

function searchPath(type: EndpointType, query: string) {
  const q = encodeURIComponent(query);
  if (type === "repository") return `/repositories?archived=false&limit=200&q=${q}`;
  if (type === "agent") return `/agents?limit=200&offset=0&q=${q}`;
  return `/search?q=${q}&archived=false&limit=20&offset=0&type=${type}`;
}

function optionTitle(option: PickOption, type: EndpointType) {
  return option.title || option.name || option.label || `${type} ${option.id.slice(0, 8)}`;
}

function endpointHref(endpoint: Schema["DependencyEndpoint"]) {
  if (endpoint.type === "item") return `#work/${endpoint.id}`;
  if (endpoint.type === "step")
    return endpoint.roadmapId
      ? `#roadmaps/${endpoint.roadmapId}/${endpoint.id}`
      : "#roadmaps";
  if (endpoint.type === "ticket") return `#tickets/${endpoint.id}`;
  if (endpoint.type === "repository") return `#repositories/${endpoint.id}`;
  return `#agents/${endpoint.id}`;
}

function endpointTitle(endpoint: Schema["DependencyEndpoint"]) {
  return endpoint.title || `${endpoint.type} ${endpoint.id.slice(0, 8)}`;
}

function EndpointPicker<T extends EndpointType>({
  label,
  types,
  value,
  onChange,
  linkedRefs = [],
  target,
  required = false,
}: {
  label: string;
  types: readonly T[];
  value: EndpointOption | null;
  onChange: (next: EndpointOption | null) => void;
  linkedRefs?: Schema["DependencyRef"][];
  target?: Pick<Schema["DependencyRef"], "type" | "id">;
  required?: boolean;
}) {
  const [type, setType] = useState<T>(types[0]);
  const optionPath = (query: string) => searchPath(type, query);
  return (
    <div className="dependency-picker">
      <label>
        <span>{label} type</span>
        <select
          aria-label={`${label} type`}
          value={type}
          onChange={(event) => {
            setType(event.target.value as T);
            onChange(null);
          }}
        >
          {types.map((entry) => (
            <option value={entry} key={entry}>
              {entry}
            </option>
          ))}
        </select>
      </label>
      {value ? (
        <p className="dependency-picked">
          Selected: <strong>{value.title}</strong>{" "}
          <button type="button" className="quiet" onClick={() => onChange(null)}>
            Clear
          </button>
        </p>
      ) : (
        <LinkPicker<PickOption>
          action={`Find ${label.toLowerCase()}`}
          label={label}
          placeholder={`Search ${type}…`}
          path={optionPath}
          linked={(option) =>
            (target?.type === type && target.id === option.id) ||
            linkedRefs.some((entry) => entry.type === type && entry.id === option.id)
          }
          filter={type === "agent" ? (option) => option.active === true : undefined}
          option={(option) => <span>{optionTitle(option, type)}</span>}
          none={`No matching ${type} found.`}
          onPick={(option) =>
            onChange({ type, id: option.id, title: optionTitle(option, type) })
          }
        />
      )}
      {required && !value && <small className="muted">Choose one to continue.</small>}
    </div>
  );
}

function DependencyRow({
  link,
  direction,
  onConfirm,
  onArchive,
  pending,
}: {
  link: Schema["DependencyLink"];
  direction: "prerequisite" | "dependent";
  onConfirm: (evidence: string) => void;
  onArchive: () => void;
  pending: boolean;
}) {
  const related = direction === "prerequisite" ? link.prerequisite : link.dependent;
  const [evidence, setEvidence] = useState("");
  const requiresHumanConfirmation =
    link.kind === "wait" ||
    link.dependent.type === "agent" ||
    link.prerequisite.type === "agent" ||
    link.prerequisite.type === "repository";
  const canConfirm =
    requiresHumanConfirmation &&
    link.kind !== "catalog" &&
    link.revision !== null &&
    link.state !== "satisfied" &&
    (link.state === "waiting" ||
      link.state === "unavailable" ||
      (link.state === "linked" && link.confirmation === null));
  const canArchive = link.kind !== "catalog" && link.revision !== null;
  return (
    <li className="line dependency-row">
      <div>
        <a href={endpointHref(related)}>{endpointTitle(related)}</a>
        {related.type === "repository" && related.mergedInto && (
          <small className="dependency-survivor">
            Merged into <a href={`#repositories/${related.mergedInto}`}>surviving repository</a>
          </small>
        )}
        <span className={`dependency-state is-${link.state}`}>{link.state}</span>
        <span className="dependency-kind">{link.kind}</span>
        {link.condition && <p className="muted">{link.condition}</p>}
        {link.context && (
          <small>
            For <a href={endpointHref(link.context)}>{endpointTitle(link.context)}</a>
          </small>
        )}
        {link.confirmation && (
          <small className="muted">
            Confirmed by {link.confirmation.confirmedBy}
            {link.confirmation.note ? ` · ${link.confirmation.note}` : ""}
          </small>
        )}
      </div>
      {(canConfirm || canArchive) && (
        <div className="toolbar">
          {canConfirm && (
            <label className="dependency-evidence">
              <span>{link.state === "unavailable" ? "Evidence (required)" : "Confirmation note (optional)"}</span>
              <input
                aria-label={`Confirmation evidence for ${endpointTitle(related)}`}
                value={evidence}
                onChange={(event) => setEvidence(event.target.value)}
                required={link.state === "unavailable"}
              />
            </label>
          )}
          {canConfirm && (
            <button
              type="button"
              disabled={pending || (link.state === "unavailable" && !evidence.trim())}
              onClick={() => onConfirm(evidence.trim())}
            >
              Confirm resolved
            </button>
          )}
          {canArchive && (
            <button type="button" className="quiet" disabled={pending} onClick={onArchive}>
              Archive link
            </button>
          )}
        </div>
      )}
    </li>
  );
}

export function DependenciesPanel({
  target,
  disabled = false,
}: {
  target: Pick<Schema["DependencyRef"], "type" | "id">;
  disabled?: boolean;
}) {
  const path = `/dependencies?type=${encodeURIComponent(target.type)}&id=${encodeURIComponent(target.id)}`;
  const query = useData<Answer<"/api/v1/dependencies">>(path, DEPENDENCY_REFRESH_MS);
  const writes = useWrites();
  const [adding, setAdding] = useState(false);
  const [prerequisite, setPrerequisite] = useState<EndpointOption | null>(null);
  const [context, setContext] = useState<EndpointOption | null>(null);
  const [condition, setCondition] = useState("");
  const prerequisites = (query.data?.prerequisites ?? []).filter((link) => !link.archived);
  const dependents = (query.data?.dependents ?? []).filter((link) => !link.archived);
  const openCount = prerequisites.filter(
    (link) => link.state === "waiting" || link.state === "unavailable",
  ).length;
  const needContext = target.type === "agent";
  const existingSharedPrerequisites = needContext
    ? []
    : prerequisites
        .filter((link) => link.kind === "shared")
        .map((link) => link.prerequisite);
  const requiresCondition =
    target.type === "agent" ||
    prerequisite?.type === "agent" ||
    prerequisite?.type === "repository";

  const create = async () => {
    if (!prerequisite || (needContext && !context) || (requiresCondition && !condition.trim())) return;
    const body: Schema["DependencyCreate"] = {
      dependent: { type: target.type, id: target.id },
      prerequisite: { type: prerequisite.type, id: prerequisite.id },
      condition: condition.trim(),
      ...(context ? { context: { type: context.type as ContextType, id: context.id } } : {}),
    };
    await writes
      .mutateAsync(() => commands.createDependency(body))
      .then(() => {
        setPrerequisite(null);
        setContext(null);
        setCondition("");
        setAdding(false);
      })
      .catch(() => undefined);
  };

  const confirm = (link: Schema["DependencyLink"], note: string) =>
    writes.mutate(() =>
      commands.confirmDependency(link.id, {
        revision: link.revision!,
        note,
      }),
    );
  const archive = (link: Schema["DependencyLink"]) =>
    writes.mutate(() => commands.archiveDependency(link.id, { revision: link.revision! }));

  return (
    <section className="dependencies-panel" aria-label="Dependencies">
      <div className="section-heading">
        <h2>
          Dependencies {openCount > 0 && <span className="count">{openCount} open</span>}
        </h2>
        {!disabled && (
          <button type="button" onClick={() => setAdding((shown) => !shown)}>
            {adding ? "Cancel" : needContext ? "Add agent dependency" : "Add prerequisite"}
          </button>
        )}
      </div>
      <ErrorMessage error={query.error || writes.error} />
      {adding && !disabled && (
        <form
          className="dependency-form"
          onSubmit={(event) => {
            event.preventDefault();
            void create();
          }}
        >
          <EndpointPicker
            label="Prerequisite"
            types={ENDPOINT_TYPES}
            value={prerequisite}
            onChange={setPrerequisite}
            linkedRefs={existingSharedPrerequisites}
            target={target}
            required
          />
          {needContext && (
            <EndpointPicker
              label="Work context for this agent dependency"
              types={CONTEXT_TYPES}
              value={context}
              onChange={setContext}
              required
            />
          )}
          <label>
            <span>Condition{requiresCondition ? " (required)" : ""}</span>
            <input
              value={condition}
              onChange={(event) => setCondition(event.target.value)}
              placeholder="What needs to happen?"
            />
          </label>
          <button
            type="submit"
            disabled={
              writes.isPending ||
              !prerequisite ||
              (needContext && !context) ||
              (requiresCondition && !condition.trim())
            }
          >
            {needContext ? "Create agent dependency" : "Create dependency"}
          </button>
        </form>
      )}
      {query.isPending && <p className="muted">Loading dependencies…</p>}
      {!query.isPending && prerequisites.length === 0 && dependents.length === 0 && (
        <Empty>No dependencies recorded.</Empty>
      )}
      {prerequisites.length > 0 && (
        <section>
          <h3>Depends on</h3>
          <ul className="dependency-list">
            {prerequisites.map((link) => (
              <DependencyRow
                key={link.id}
                link={link}
                direction="prerequisite"
                pending={writes.isPending}
                onConfirm={(note) => confirm(link, note)}
                onArchive={() => archive(link)}
              />
            ))}
          </ul>
        </section>
      )}
      {dependents.length > 0 && (
        <section>
          <h3>Required by</h3>
          <ul className="dependency-list">
            {dependents.map((link) => (
              <DependencyRow
                key={link.id}
                link={link}
                direction="dependent"
                pending={writes.isPending}
                onConfirm={(note) => confirm(link, note)}
                onArchive={() => archive(link)}
              />
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}

export function DependencyCount({ count }: { count: number }) {
  return count > 0 ? (
    <span className="dependency-count" aria-label={`${count} open dependencies`}>
      {count} open {count === 1 ? "dependency" : "dependencies"}
    </span>
  ) : null;
}

export function LiveDependencyWaits({
  roadmapId,
  roadmapIds,
  paused,
  refreshMs,
}: {
  roadmapId?: string;
  roadmapIds?: string[];
  paused: boolean;
  refreshMs: number;
}) {
  const ids = roadmapId ? [roadmapId] : (roadmapIds ?? []).filter(Boolean);
  const suffix =
    ids.length === 1
      ? `?roadmapId=${encodeURIComponent(ids[0])}`
      : ids.length > 1
        ? `?${ids.map((id) => `roadmapIds=${encodeURIComponent(id)}`).join("&")}`
        : "";
  const query = useData<Answer<"/api/v1/dependencies/waits">>(
    `/dependencies/waits${suffix}`,
    paused ? undefined : refreshMs,
  );
  const items = query.data?.items ?? [];
  return (
    <>
      <ErrorMessage error={query.error} />
      {items.length > 0 && (
        <section className="live-waits" aria-label="Open agent waits">
          <h2>Open agent waits <span className="count">{items.length}</span></h2>
          <ul className="dependency-list">
            {items.map((wait) => (
              <li className="line dependency-row" key={wait.id}>
                <div>
                  <a href={endpointHref(wait.dependent)}>{endpointTitle(wait.dependent)}</a>{" "}
                  <span>is waiting on</span>{" "}
                  <a href={endpointHref(wait.prerequisite)}>{endpointTitle(wait.prerequisite)}</a>
                  {wait.context && (
                    <small>
                      {" "}for <a href={endpointHref(wait.context)}>{endpointTitle(wait.context)}</a>
                    </small>
                  )}
                  {wait.condition && <p className="muted">{wait.condition}</p>}
                </div>
                <a className="quiet" href={endpointHref(wait.dependent)}>Review wait</a>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
