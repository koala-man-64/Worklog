import type { ItemStatus, Status } from "../apiTypes";
/** A ticket's or a roadmap step's statuses, in the order menus list them. */
export const STATUSES: readonly Status[] = ["open", "doing", "blocked", "done"];
/** A work item's statuses: Waiting is work that waits on someone else. */
export const ITEM_STATUSES: readonly ItemStatus[] = [
  "open",
  "doing",
  "waiting",
  "blocked",
  "done",
];
/**
 * Active, the calculated status: an agent reported work on the record within two hours while its
 * status is Open or Doing. Worklog works it out on each read (`agentActive`); nobody sets it, so
 * no menu offers it.
 */
export type ShownStatus<S extends string = ItemStatus> = S | "active";
/** A work item's statuses as the work list filters them: Active beside the ones the person sets. */
export const SHOWN_ITEM_STATUSES: readonly ShownStatus[] = [
  "open",
  "doing",
  "active",
  "waiting",
  "blocked",
  "done",
];
export const ACTIVE_TITLE =
  "Active: an agent reported work on it in the last two hours. Worklog works this out; the status menu keeps the status you set.";
/** The status a record shows: Active stands in for Open or Doing while an agent works on it. */
export const shownStatus = <S extends string>(record: {
  status: S;
  agentActive?: boolean;
}): ShownStatus<S> => (record.agentActive ? "active" : record.status);
/** A status as a label; a step's may be any text, since steps.status has no CHECK. */
export const statusLabel = (status: string) =>
  status[0].toUpperCase() + status.slice(1);
/** The Active badge beside a status menu, which keeps the status the person set; nothing when the record isn't Active. */
export function ActiveBadge({ active }: { active?: boolean }) {
  if (!active) return null;
  return (
    <span className="active-badge" title={ACTIVE_TITLE}>
      <span className="status-dot active" aria-hidden="true" />
      Active
    </span>
  );
}
/** A status menu over `options`: ITEM_STATUSES for work, STATUSES for tickets and steps. */
export function StatusSelect<S extends ItemStatus>({
  value,
  options,
  onChange,
  label = "Status",
  disabled = false,
}: {
  /** What the record holds now; a step's status may be one the menu doesn't offer. */
  value: string;
  options: readonly S[];
  onChange: (value: S) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <select
      className={"status-select " + value}
      data-status={value}
      disabled={disabled}
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as S)}
    >
      {options.map((s) => (
        <option key={s} value={s}>
          {statusLabel(s)}
        </option>
      ))}
    </select>
  );
}
