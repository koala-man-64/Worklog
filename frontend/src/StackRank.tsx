import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { commands } from "./commands";
import { ErrorMessage } from "./components";
import { refreshAfterWrite } from "./hooks";
import type { Item } from "./types";

/**
 * Moves an item to a stack rank: 1 is the work that matters most. The move is checked against
 * the item's own revision. A refused move (the item changed meanwhile) refreshes as well as a
 * done one, so the next try starts from what is there now.
 */
export function useRank() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      rank,
      revision,
    }: {
      id: string;
      rank: number;
      revision: number;
    }) => commands.rankItem(id, { revision, rank }),
    onSettled: () => refreshAfterWrite(client),
  });
}

/** An item's stack rank where it can't be changed, as on Today. */
export function RankBadge({ rank }: { rank?: number | null }) {
  if (!rank) return null;
  return (
    <span className="rank-badge">
      <span aria-hidden="true">#{rank}</span>
      <span className="visually-hidden">Stack rank {rank}</span>
    </span>
  );
}

/** The button that shows an item's rank and opens its picker; unranked work shows none. */
export function RankButton({
  item,
  open,
  onToggle,
  suffix,
  disabled = false,
}: {
  item: Item;
  open: boolean;
  onToggle: () => void;
  /** Shown after the number, such as "of 42". */
  suffix?: string;
  disabled?: boolean;
}) {
  if (!item.rank) return null;
  return (
    <button
      type="button"
      className="quiet rank"
      title="Change stack rank"
      aria-label={`Change stack rank of ${item.title}, now ${item.rank}`}
      aria-expanded={open}
      disabled={disabled}
      onClick={onToggle}
    >
      #{item.rank}
      {suffix && <span className="rank-of"> {suffix}</span>}
    </button>
  );
}

/** Picks a new stack rank for an item: Move takes it there, Cancel or Escape closes. */
export function RankPicker({
  item,
  rankedCount,
  onClose,
}: {
  item: Item;
  rankedCount: number;
  onClose: () => void;
}) {
  const [value, setValue] = useState(String(item.rank ?? ""));
  const rank = useRank();
  const target = Number(value);
  const valid = Number.isInteger(target) && target >= 1;
  return (
    <form
      className="inline rank-picker"
      onSubmit={(e) => {
        e.preventDefault();
        if (!valid) return;
        rank.mutate(
          {
            id: item.id,
            rank: Math.min(target, rankedCount),
            revision: item.revision,
          },
          { onSuccess: onClose },
        );
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <span>Move to rank</span>
      <input
        type="number"
        inputMode="numeric"
        min={1}
        max={rankedCount}
        step={1}
        aria-label={"New stack rank for " + item.title}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoFocus
        required
      />
      <span className="muted">of {rankedCount}</span>
      <button
        disabled={
          !valid ||
          Math.min(target, rankedCount) === item.rank ||
          rank.isPending
        }
      >
        Move
      </button>
      <button type="button" className="quiet" onClick={onClose}>
        Cancel
      </button>
      <ErrorMessage error={rank.error} />
    </form>
  );
}
