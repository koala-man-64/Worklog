import { useState } from "react";
import { useData } from "../hooks";
import { ErrorMessage } from "../ui/Messages";
export function History({ items }: { items: unknown[] }) {
  return (
    <details>
      <summary>Activity history ({items?.length ?? 0})</summary>
      <pre>{JSON.stringify(items, null, 2)}</pre>
    </details>
  );
}
/**
 * Each change's full before and after records repeat the whole record, so the history
 * of a long one that changes often is large: it loads only when opened. A record whose
 * page doesn't count its changes leaves `changes` out.
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
  const history = useData<{ items: unknown[] }>(
    "/history?entityId=" + encodeURIComponent(id),
  );
  return (
    <>
      <ErrorMessage error={history.error} />
      {history.isPending ? (
        <p>Loading history…</p>
      ) : (
        <pre>{JSON.stringify(history.data?.items ?? [], null, 2)}</pre>
      )}
    </>
  );
}
