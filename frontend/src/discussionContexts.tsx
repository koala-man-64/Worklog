import { useQuery } from "@tanstack/react-query";
import { TIE_TYPES, tieHref, type TieType } from "./agentTieKinds";
import { getDataContext, request } from "./api";
import type {
  DiscussionContext,
  DiscussionContextType,
  SearchResults,
} from "./apiTypes";

/**
 * A record a discussion is tagged with, or a draft will tag it with: its kind and id, and what a
 * page has read of it. A thread's own tags carry more.
 */
export type ContextRef = Pick<DiscussionContext, "type" | "id"> &
  Partial<Pick<DiscussionContext, "title" | "archived" | "roadmapId">>;
export type ContextChoice = ContextRef & { label: string };

export function useContextSearch(query: string) {
  return useQuery({
    queryKey: [getDataContext(), "discussion-context-search", query.trim()],
    queryFn: () =>
      request<SearchResults>(
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
  results: SearchResults["items"];
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

/** Where a tagged record opens, as a tie to it opens; a step needs its roadmap. */
function contextHref(
  context: ContextRef,
  roadmapId: string | undefined,
): string | undefined {
  if (context.type === "release") return "#releases/" + context.id;
  if (context.type === "step" && !roadmapId) return undefined;
  if (!(TIE_TYPES as readonly string[]).includes(context.type))
    return undefined;
  return tieHref(context.type as TieType, context.id, { roadmapId });
}

export function ContextReference({
  context,
  label,
}: {
  context: ContextRef;
  label?: string;
}) {
  const detail = useContextDetail(context);
  const title =
    label ||
    context.title ||
    detail.title ||
    `${context.type} ${context.id.slice(0, 8)}`;
  const href = contextHref(context, context.roadmapId ?? detail.roadmapId);
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

/** A step's title and roadmap, read only when the discussion didn't bring them, as a draft's
 * chosen context doesn't. */
function useContextDetail(context: ContextRef) {
  const path = `/steps/${context.id}`;
  const query = useQuery({
    queryKey: [getDataContext(), "discussion-context-detail", path],
    queryFn: () => request<{ title?: string; roadmapId?: string }>(path),
    enabled: context.type === "step" && !context.roadmapId,
    staleTime: 60_000,
  });
  return {
    title: context.type === "step" ? query.data?.title : undefined,
    roadmapId: context.type === "step" ? query.data?.roadmapId : undefined,
  };
}
