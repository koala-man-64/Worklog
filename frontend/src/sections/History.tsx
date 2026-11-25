import { useState } from "react";
import { useData } from "../hooks";
import { ErrorMessage } from "../ui/Messages";
import type { Answer } from "../apiTypes";

type Listed = Answer<"/api/v1/history">["items"][number];

/**
 * A record's history loads only when opened, as a list of who changed it, how and when. Each
 * change's before and after records can repeat the whole record, so one loads only when that
 * change is opened. A record whose page doesn't count its changes leaves `changes` out.
 */
export function ChangeHistory({
  id,
  changes,
}: {
  id: string;
  changes?: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        Activity history{changes === undefined ? "" : ` (${changes})`}
      </summary>
      {open && <Changes id={id} />}
    </details>
  );
}
function Changes({ id }: { id: string }) {
  const history = useData<Answer<"/api/v1/history">>(
    "/history?entityId=" + encodeURIComponent(id),
  );
  if (history.isPending)
    return (
      <>
        <ErrorMessage error={history.error} />
        <p>Loading history…</p>
      </>
    );
  const items = history.data?.items ?? [];
  return (
    <>
      <ErrorMessage error={history.error} />
      {items.length === 0 ? (
        <p>No changes recorded.</p>
      ) : (
        <ol className="change-list" aria-label="Changes">
          {items.map((change) => (
            <li key={change.id}>
              <time dateTime={change.createdAt}>
                {new Date(change.createdAt).toLocaleString()}
              </time>{" "}
              <span className="change-actor">{change.actorLabel}</span>{" "}
              <code className="change-kind">{change.kind}</code>
              <Change entityId={id} change={change} />
            </li>
          ))}
        </ol>
      )}
    </>
  );
}
function Change({ entityId, change }: { entityId: string; change: Listed }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Show the change</summary>
      {open && <ChangeRecords entityId={entityId} eventId={change.id} />}
    </details>
  );
}
function ChangeRecords({
  entityId,
  eventId,
}: {
  entityId: string;
  eventId: string;
}) {
  const detail = useData<Answer<"/api/v1/history">>(
    `/history?entityId=${encodeURIComponent(entityId)}&eventId=${encodeURIComponent(eventId)}`,
  );
  const [whole] = detail.data?.items ?? [];
  return (
    <>
      <ErrorMessage error={detail.error} />
      {detail.isPending || !whole ? (
        <p>Loading the change…</p>
      ) : (
        <pre>
          {JSON.stringify(
            "before" in whole
              ? { before: whole.before, after: whole.after }
              : whole,
            null,
            2,
          )}
        </pre>
      )}
    </>
  );
}
