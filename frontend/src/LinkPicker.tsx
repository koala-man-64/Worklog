import {
  useEffect,
  useId,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getDataContext, request } from "./api";
/** What a picker reads: any list route's items, of the kind of record it picks from. */
type Page<T> = { items: T[]; total: number };

type LinkPickerProps<T extends { id: string }> = {
  /** The button's text, such as "Link repository". */
  action: string;
  /** Names the search box ("… search") and its matches ("… matches"). */
  label: string;
  placeholder: string;
  /** Where the matches for a search are read. */
  path: (q: string) => string;
  /** Matches what was typed here, for a route that returns its whole list rather than searching. */
  filter?: (record: T, q: string) => boolean;
  /** Records already linked are not offered again. */
  linked: (record: T) => boolean;
  option: (record: T) => ReactNode;
  /** What to say when there is nothing at all to link. */
  none: ReactNode;
  onPick: (record: T) => void;
  disabled?: boolean;
  /** Lays the search over what follows instead of pushing it down, for one-line forms. */
  floating?: boolean;
};
// Whether a node is the button or inside its open search. The search is found by its id,
// not a ref: its input takes focus, and the button loses it, before React attaches the
// refs of a search that just opened.
function within(toggle: HTMLElement | null, searchId: string, node: Node) {
  return (
    !!toggle?.contains(node) ||
    !!document.getElementById(searchId)?.contains(node)
  );
}
/**
 * A button that opens a search for something to link. Picking a match links it and
 * closes the search. In the search, Enter picks the first match and Escape closes it;
 * neither submits a form the picker sits in. The button and the search are siblings in
 * the surrounding layout, so opening the search never moves the button.
 */
export function LinkPicker<T extends { id: string }>(
  props: LinkPickerProps<T>,
) {
  const { action, disabled = false, floating = false } = props;
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const searchId = useId();
  // A search that something else disabled stays closed, rather than taking focus back
  // when it is enabled again.
  if (disabled && open) setOpen(false);
  const shown = open && !disabled;
  // A floating search closes on a click anywhere else.
  useEffect(() => {
    if (!shown || !floating) return;
    const away = (e: MouseEvent) => {
      if (!within(toggle.current, searchId, e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [shown, floating, searchId]);
  const close = () => {
    setOpen(false);
    toggle.current?.focus();
  };
  const escape = (e: KeyboardEvent) => {
    if (e.key !== "Escape" || !shown) return;
    e.preventDefault();
    e.stopPropagation();
    close();
  };
  // Tabbing out of a floating search closes it, so it never covers what has focus.
  const leave = (e: FocusEvent) => {
    const next = e.relatedTarget as Node | null;
    if (floating && next && !within(toggle.current, searchId, next))
      setOpen(false);
  };
  return (
    <>
      <button
        type="button"
        ref={toggle}
        aria-expanded={shown}
        aria-controls={shown ? searchId : undefined}
        disabled={disabled}
        onClick={() => setOpen(!open)}
        onKeyDown={escape}
        onBlur={leave}
      >
        {action}
      </button>
      {shown && (
        <LinkSearch
          {...props}
          id={searchId}
          onKeyDown={escape}
          onBlur={leave}
          onPick={(record) => {
            props.onPick(record);
            close();
          }}
        />
      )}
    </>
  );
}
function LinkSearch<T extends { id: string }>({
  id,
  label,
  placeholder,
  path,
  filter,
  linked,
  option,
  none,
  onPick,
  floating,
  onKeyDown,
  onBlur,
}: LinkPickerProps<T> & {
  id: string;
  onKeyDown: (e: KeyboardEvent) => void;
  onBlur: (e: FocusEvent) => void;
}) {
  const [q, setQ] = useState("");
  const source = path(q.trim());
  // The last matches stay up while the next search loads, so typing doesn't flicker.
  const query = useQuery({
    queryKey: [getDataContext(), source],
    queryFn: () => request<Page<T>>(source),
    placeholderData: keepPreviousData,
  });
  const all = query.data?.items ?? [];
  const items = filter ? all.filter((record) => filter(record, q.trim())) : all;
  const offered = items.filter((record) => !linked(record));
  const total = filter ? items.length : (query.data?.total ?? 0);
  return (
    <div
      className={"link-search" + (floating ? " floating" : "")}
      id={id}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
    >
      <input
        aria-label={label + " search"}
        placeholder={placeholder}
        autoFocus
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          e.preventDefault();
          // Only a match for what is typed now: not one left over from the last search.
          if (!query.isPlaceholderData && offered[0]) onPick(offered[0]);
        }}
      />
      {query.error ? (
        <p role="alert">
          Could not load matches.{" "}
          <button type="button" onClick={() => void query.refetch()}>
            Retry
          </button>
        </p>
      ) : !query.data ? (
        <p className="muted">Searching…</p>
      ) : !items.length ? (
        <p className="muted">
          {q.trim() ? "Nothing matches this search." : none}
        </p>
      ) : !offered.length ? (
        <p className="muted">Every match is already linked.</p>
      ) : (
        <ul aria-label={label + " matches"}>
          {offered.map((record) => (
            <li key={record.id}>
              <button type="button" onClick={() => onPick(record)}>
                {option(record)}
              </button>
            </li>
          ))}
        </ul>
      )}
      {total > items.length && (
        <small className="muted">
          Showing {items.length} of {total}. Keep typing to narrow.
        </small>
      )}
    </div>
  );
}
