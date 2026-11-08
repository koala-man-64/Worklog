import { useRef, useState } from "react";
import type { TieType } from "../agentTieKinds";
import { commands } from "../commands";
import { useData, useWrites } from "../hooks";
import type { Answer, PersonAssignment } from "../apiTypes";
import { ErrorMessage } from "../ui/Messages";
/** A person's title and team on one line, such as "Staff Engineer · Platform"; empty when they have neither. */
export function personLine(title: string, team: string) {
  return [title, team].filter(Boolean).join(" · ");
}
/**
 * A tie's role, saved when its field loses focus, and the tie's Unlink. Pressing Unlink
 * right after editing the role saves the role first: the press is not lost to a button
 * disabled mid-click, and the save cannot land after the unlink and tie the person again.
 * A person's tie goes to /person-links, an agent's to /agent-links.
 */
export function TieRole({
  tie,
  name,
  unlinkName,
  relationship,
  disabled = false,
  unlinkDisabled = false,
  placeholder,
}: {
  tie:
    | {
        personId: string;
        targetType: "item" | "roadmap" | "repository";
        targetId: string;
      }
    | { agentId: string; targetType: TieType; targetId: string };
  /** The role field's accessible name, such as "Role on Define SLOs". */
  name: string;
  unlinkName: string;
  relationship: string;
  disabled?: boolean;
  unlinkDisabled?: boolean;
  placeholder: string;
}) {
  const action = useWrites();
  const saving = useRef<Promise<unknown>>(Promise.resolve());
  const [unlinking, setUnlinking] = useState(false);
  const save = (next: string): Promise<unknown> =>
    "agentId" in tie
      ? commands.tieAgent({ ...tie, relationship: next })
      : commands.linkPerson({ ...tie, relationship: next });
  const unlink = (): Promise<unknown> =>
    "agentId" in tie ? commands.untieAgent(tie) : commands.unlinkPerson(tie);
  return (
    <>
      <input
        aria-label={name}
        defaultValue={relationship}
        key={relationship}
        disabled={disabled}
        placeholder={placeholder}
        onBlur={(e) => {
          const next = e.target.value.trim();
          if (next !== relationship)
            saving.current = action
              .mutateAsync(() => save(next))
              .catch(() => undefined);
        }}
      />
      <button
        type="button"
        className="quiet"
        aria-label={unlinkName}
        disabled={unlinkDisabled || unlinking}
        onClick={() => {
          setUnlinking(true);
          void saving.current
            .then(() => action.mutateAsync(unlink))
            .catch(() => undefined)
            .finally(() => setUnlinking(false));
        }}
      >
        Unlink
      </button>
      <ErrorMessage error={action.error} />
    </>
  );
}
export function PersonBadges({ people }: { people?: PersonAssignment[] }) {
  if (!people?.length) return null;
  return (
    <span className="person-badges">
      {people.map((p) => (
        <a
          key={p.id}
          className="person-badge"
          href={"#people/" + p.personId}
          title={personLine(p.personTitle, p.personTeam) || undefined}
        >
          {p.name}
          {p.relationship ? ` (${p.relationship})` : ""}
        </a>
      ))}
    </span>
  );
}
export function PeopleLinks({
  targetType,
  targetId,
  people = [],
  disabled = false,
  canTie = !disabled,
  headingLevel = 3,
}: {
  targetType: "item" | "roadmap" | "repository";
  targetId: string;
  people?: PersonAssignment[];
  disabled?: boolean;
  /** False keeps the ties' roles editable and their Unlink, but takes no new tie, as on an archived repository. */
  canTie?: boolean;
  /** 2 where the page's other sections are h2, as on a repository's page. */
  headingLevel?: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const directory = useData<Answer<"/api/v1/people">>("/people?archived=false");
  const action = useWrites();
  const [personId, setPersonId] = useState("");
  const [newName, setNewName] = useState("");
  const [relationship, setRelationship] = useState("");
  const [error, setError] = useState("");
  return (
    <section>
      <Heading>
        People <span className="count">{people.length}</span>
      </Heading>
      {people.map((p) => (
        <div className="line person-assignment" key={p.id}>
          <a href={"#people/" + p.personId}>{p.name}</a>
          {personLine(p.personTitle, p.personTeam) && (
            <small>{personLine(p.personTitle, p.personTeam)}</small>
          )}
          <TieRole
            tie={{ personId: p.personId, targetType, targetId }}
            name={"Role for " + p.name}
            unlinkName={"Unlink " + p.name}
            relationship={p.relationship}
            disabled={disabled}
            unlinkDisabled={disabled}
            placeholder="Role (e.g. Owner, Stakeholder)…"
          />
        </div>
      ))}
      {canTie && (
        <form
          className="inline"
          onSubmit={async (e) => {
            e.preventDefault();
            if (disabled || !personId) return;
            setError("");
            try {
              let resolvedPersonId = personId;
              if (personId === "__new__") {
                if (!newName.trim()) return;
                const created = await commands.createPerson({
                  name: newName.trim(),
                });
                resolvedPersonId = created.id;
              }
              await action.mutateAsync(() =>
                commands.linkPerson({
                  personId: resolvedPersonId,
                  targetType,
                  targetId,
                  relationship: relationship.trim(),
                }),
              );
              setPersonId("");
              setNewName("");
              setRelationship("");
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
            }
          }}
        >
          <select
            aria-label="Choose person"
            value={personId}
            disabled={disabled}
            onChange={(e) => setPersonId(e.target.value)}
          >
            <option value="">Select person to tie…</option>
            {/* Someone already tied changes their role in their own row instead. */}
            {directory.data?.items
              ?.filter(
                (person) => !people.some((p) => p.personId === person.id),
              )
              .map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name}
                  {personLine(person.title, person.team)
                    ? " (" + personLine(person.title, person.team) + ")"
                    : ""}
                </option>
              ))}
            <option value="__new__">+ New person…</option>
          </select>
          {personId === "__new__" && (
            <input
              aria-label="New person name"
              placeholder="New person name…"
              value={newName}
              disabled={disabled}
              onChange={(e) => setNewName(e.target.value)}
              required
            />
          )}
          <input
            aria-label="Relationship or role"
            placeholder="Role (e.g. Owner, Stakeholder, Reviewer)…"
            value={relationship}
            disabled={disabled}
            onChange={(e) => setRelationship(e.target.value)}
          />
          <button
            type="submit"
            disabled={
              disabled ||
              action.isPending ||
              !personId ||
              (personId === "__new__" && !newName.trim())
            }
          >
            Tie person
          </button>
        </form>
      )}
      <ErrorMessage error={error || action.error} />
    </section>
  );
}
