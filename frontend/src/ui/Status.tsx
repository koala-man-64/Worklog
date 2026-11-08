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
/** A status as a label; a step's may be any text, since steps.status has no CHECK. */
export const statusLabel = (status: string) =>
  status[0].toUpperCase() + status.slice(1);
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
