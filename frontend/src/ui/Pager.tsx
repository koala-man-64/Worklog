/**
 * Moves through a list a page at a time: Previous and Next either side of the rows that show, as
 * "51 to 100 of 120". A list that fits on its first page, or holds nothing, has no pager.
 */
export function Pager({
  offset,
  total,
  onChange,
  label,
  limit = 50,
  className = "toolbar",
}: {
  offset: number;
  total: number;
  onChange: (offset: number) => void;
  /** What the list holds, as its buttons say: "Previous roles", "Next roles". */
  label: string;
  /** How many rows a page holds. */
  limit?: number;
  /** How the pager sits where its list is, such as in a sheet's footer. */
  className?: string;
}) {
  // A total of 0 has no rows on any page, and is what a list reads as while it loads.
  if (total === 0 || (total <= limit && offset === 0)) return null;
  return (
    <div
      className={className ? "pager " + className : "pager"}
      role="group"
      aria-label={label + " pages"}
    >
      <button
        type="button"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        Previous {label}
      </button>
      <span>
        {offset + 1} to {Math.min(offset + limit, total)} of {total}
      </span>
      <button
        type="button"
        disabled={offset + limit >= total}
        onClick={() => onChange(offset + limit)}
      >
        Next {label}
      </button>
    </div>
  );
}
