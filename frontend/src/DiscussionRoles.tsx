import { useEffect, useState, type FormEvent } from "react";
import { Empty, ErrorMessage } from "./components";
import {
  ContextPicker,
  ContextReference,
  useContextSearch,
  type PickedContext,
} from "./discussionContexts";
import { useWrites } from "./hooks";
import { commands } from "./commands";
import type { DiscussionParticipants, DiscussionRole } from "./apiTypes";

export function DiscussionRolesPanel({
  roles,
  total,
  offset,
  limit,
  onPage,
  onCreated,
  participants,
  error,
  isPending,
}: {
  roles: DiscussionRole[];
  total: number;
  offset: number;
  limit: number;
  onPage: (offset: number) => void;
  onCreated: () => void;
  participants?: DiscussionParticipants;
  error: unknown;
  isPending: boolean;
}) {
  const action = useWrites();
  const [contextQuery, setContextQuery] = useState("");
  const [roleContext, setRoleContext] = useState<PickedContext | null>(null);
  const [roleLabel, setRoleLabel] = useState("");
  const [roleError, setRoleError] = useState<unknown>(null);
  const contextSearch = useContextSearch(contextQuery);

  async function createRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!roleContext || !roleLabel.trim()) return;
    setRoleError(null);
    try {
      await action.mutateAsync(() =>
        commands.createRole({
          contextType: roleContext.type,
          contextId: roleContext.id,
          label: roleLabel.trim(),
        }),
      );
      onCreated();
      setRoleContext(null);
      setContextQuery("");
      setRoleLabel("");
    } catch (failure) {
      setRoleError(failure);
    }
  }

  return (
    <details className="discussion-roles-panel">
      <summary>Discussion roles ({roles.length})</summary>
      <div className="discussion-roles-body">
        <ErrorMessage error={error || action.error || roleError} />
        {isPending ? (
          <p>Loading roles…</p>
        ) : !roles.length ? (
          <Empty>No roles have been created.</Empty>
        ) : (
          <>
            <ul className="discussion-role-list">
              {roles.map((role) => (
                <DiscussionRoleRow
                  key={role.id}
                  role={role}
                  participants={participants}
                  action={action}
                />
              ))}
            </ul>
            {total > limit && (
              <div className="discussion-pagination">
                <button
                  type="button"
                  disabled={offset <= 0}
                  onClick={() => onPage(Math.max(0, offset - limit))}
                >
                  Previous roles
                </button>
                <small>
                  {offset + 1} to {Math.min(offset + limit, total)} of {total}
                </small>
                <button
                  type="button"
                  disabled={offset + limit >= total}
                  onClick={() => onPage(offset + limit)}
                >
                  More roles
                </button>
              </div>
            )}
          </>
        )}
        <details className="discussion-create-role">
          <summary>Create an unassigned role</summary>
          <form onSubmit={createRole}>
            <ContextPicker
              label="Role context"
              query={contextQuery}
              onQueryChange={setContextQuery}
              selected={roleContext ? [roleContext] : []}
              onAdd={(choice) => {
                setRoleContext(choice);
                setContextQuery("");
              }}
              onRemove={() => setRoleContext(null)}
              results={contextSearch.data?.items ?? []}
              loading={contextSearch.isFetching}
              single
            />
            <label>
              Role name
              <input
                value={roleLabel}
                onChange={(event) => setRoleLabel(event.target.value)}
                required
                maxLength={200}
              />
            </label>
            <button
              disabled={!roleContext || !roleLabel.trim() || action.isPending}
            >
              Create role
            </button>
          </form>
        </details>
        <p className="muted">
          Agent claims, offers, and acceptance happen through authenticated
          agent actions. This view shows the current owner and pending offer.
        </p>
      </div>
    </details>
  );
}

function DiscussionRoleRow({
  role,
  participants,
  action,
}: {
  role: DiscussionRole;
  participants?: DiscussionParticipants;
  action: ReturnType<typeof useWrites>;
}) {
  const [ownerId, setOwnerId] = useState(role.ownerId ?? "");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => setOwnerId(role.ownerId ?? ""), [role.ownerId]);

  async function reassign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      await action.mutateAsync(() =>
        commands.reassignRole(role.id, {
          revision: role.revision,
          ownerId: ownerId || null,
          reason: reason.trim(),
        }),
      );
      setReason("");
    } catch (failure) {
      setError(failure);
    }
  }

  const ownerOptions =
    participants?.items.filter(
      (participant) => participant.kind === "agent" && participant.active,
    ) ?? [];
  const ownerUnchanged = ownerId === (role.ownerId ?? "");
  return (
    <li
      className="discussion-role-card"
      data-testid={`discussion-role-${role.id}`}
    >
      <div className="discussion-role-head">
        <strong>{role.label}</strong>
      </div>
      <ContextReference
        context={{ type: role.contextType, id: role.contextId }}
        label={role.contextTitle}
      />
      <p>
        Owner: <strong>{role.ownerLabel ?? "Unassigned"}</strong>
      </p>
      {role.offer ? (
        <div className="role-offer" aria-label="Pending handoff offer">
          <strong>Handoff offered to {role.offer.toLabel}</strong>
          {role.offer.note && <p>{role.offer.note}</p>}
        </div>
      ) : (
        <p className="muted">No pending handoff offer.</p>
      )}
      <details>
        <summary>Reassign owner</summary>
        <form onSubmit={reassign}>
          <label>
            New owner
            <select
              value={ownerId}
              onChange={(event) => setOwnerId(event.target.value)}
            >
              <option value="">Unassigned</option>
              {ownerOptions.map((participant) => (
                <option key={participant.id} value={participant.id}>
                  {participant.label} ({participant.kind})
                  {!participant.active ? " · inactive" : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            Reason for reassignment
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              required
              maxLength={2000}
            />
          </label>
          <button
            disabled={action.isPending || ownerUnchanged || !reason.trim()}
          >
            Reassign role
          </button>
          <ErrorMessage error={error} />
        </form>
      </details>
    </li>
  );
}
