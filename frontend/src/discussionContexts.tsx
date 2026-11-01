import { useQuery } from "@tanstack/react-query";
import { getDataContext, request } from "./api";
import type {
  DiscussionContext,
  DiscussionContextType,
  DiscussionSearchResults,
} from "./discussionTypes";

export type ContextChoice = DiscussionContext & { label: string };

export function useContextSearch(query: string) {
  return useQuery({
    queryKey: [getDataContext(), "discussion-context-search", query.trim()],
    queryFn: () =>
      request<DiscussionSearchResults>(
        `/search?q=${encodeURIComponent(query.trim())}&archived=false&limit=20&offset=0`,
      ),
    enabled: query.trim().length >= 2,
    staleTime: 30_000,
  });
}

function contextType(resultType: string): DiscussionContextType | null {
  if (
    resultType === "item" ||
    resultType === "roadmap" ||
    resultType === "step"
  )
    return resultType;
  return null;
}

export function ContextPicker({
  label,
  query,
  onQueryChange,
  selected,
  onAdd,
  onRemove,
  results,
  loading,
  single = false,
}: {
  label: string;
  query: string;
  onQueryChange: (value: string) => void;
  selected: ContextChoice[];
  onAdd: (value: ContextChoice) => void;
  onRemove: (value: ContextChoice) => void;
  results: DiscussionSearchResults["items"];
  loading: boolean;
  single?: boolean;
}) {
  const options = results.filter((result) => contextType(result.type));
  return (
    <div className="discussion-context-picker">
      <label>
        {label}
        <input
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Find a work item, roadmap, or step…"
        />
      </label>
      {loading && <small className="muted">Searching…</small>}
      {query.trim().length >= 2 && !loading && !options.length && (
        <small className="muted">No matching work, roadmap, or step.</small>
      )}
      {options.length > 0 && (
        <ul className="discussion-context-suggestions">
          {options.map((result) => {
            const type = contextType(result.type)!;
            const choice = {
              type,
              id: result.id,
              label: result.title || type + " " + result.id.slice(0, 8),
            };
            const exists = selected.some(
              (selectedContext) =>
                selectedContext.type === choice.type &&
                selectedContext.id === choice.id,
            );
            return (
              <li key={type + result.id}>
                <button
                  type="button"
                  disabled={exists || (single && selected.length > 0)}
                  onClick={() => onAdd(choice)}
                >
                  <small>{type}</small> {choice.label}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {selected.length > 0 && (
        <ul
          className="discussion-selected-contexts"
          aria-label="Selected contexts"
        >
          {selected.map((choice) => (
            <li key={choice.type + choice.id}>
              <ContextReference context={choice} label={choice.label} />
              <button
                type="button"
                className="quiet"
                aria-label={`Remove ${choice.label} context`}
                onClick={() => onRemove(choice)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ContextReference({
  context,
  label,
}: {
  context: DiscussionContext;
  label?: string;
}) {
  const detail = useContextDetail(context);
  const title =
    label || detail.title || `${context.type} ${context.id.slice(0, 8)}`;
  const href =
    context.type === "item"
      ? "#work/" + context.id
      : context.type === "roadmap"
        ? "#roadmaps/" + context.id
        : detail.roadmapId
          ? `#roadmaps/${detail.roadmapId}/${context.id}`
          : undefined;
  return href ? (
    <a className="context-tag" href={href} title={context.id}>
      {title}
    </a>
  ) : (
    <span className="context-tag" title={context.id}>
      {title}
    </span>
  );
}

function useContextDetail(context: DiscussionContext) {
  const path =
    context.type === "item"
      ? `/work-items/${context.id}`
      : context.type === "roadmap"
        ? `/roadmaps/${context.id}`
        : `/steps/${context.id}`;
  const query = useQuery({
    queryKey: [getDataContext(), "discussion-context-detail", path],
    queryFn: () => request<{ title?: string; roadmapId?: string }>(path),
    enabled: context.type === "step",
    staleTime: 60_000,
  });
  return {
    title: context.type === "step" ? query.data?.title : undefined,
    roadmapId: context.type === "step" ? query.data?.roadmapId : undefined,
  };
}
