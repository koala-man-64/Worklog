import { useState, type ReactNode } from "react";
import { commands } from "./commands";
import { useAction, useCommand, useData } from "./hooks";
import {
  DraftField,
  Empty,
  ErrorMessage,
  History,
  StatusSelect,
  TieRole,
  personLine,
  statusLabel,
} from "./components";
import { LinkPicker } from "./LinkPicker";
import type { Repository } from "./repositoryTypes";
import type { Item, List, Person, PersonDetail, Roadmap } from "./types";
import { Icon } from "./icons";

type TargetType = "item" | "roadmap" | "repository";

export function People({ selected }: { selected?: string }) {
  return selected ? <PersonDetailView id={selected} /> : <PeopleList />;
}

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

/**
 * A phone number as a tel: address, or null when it would not dial the number as written.
 * An extension after the number ("ext. 12", "x12") is left off, and so is a trunk 0 written
 * after the country code ("+44 (0)20"), which is not dialled from abroad. Anything with
 * letters in it, such as 1-800-FLOWERS, is shown but never dialled.
 */
export function telephone(phone: string) {
  const [number] = phone.trim().split(/\s*(?:ext\.?|extension|x)\s*\d+$/i);
  if (!/^\+?[\d\s().\-/]+$/.test(number)) return null;
  const digits = number
    .replace(/^(\+\d{1,3})\s*\(0\)/, "$1")
    .replace(/[^\d+]/g, "");
  return (digits.match(/\d/g)?.length ?? 0) >= 3 ? "tel:" + digits : null;
}

/** How to reach someone: email and phone open the mail app and the dialer. */
export function Contact({ person }: { person: Person }) {
  const dial = telephone(person.phone);
  const parts: ReactNode[] = [];
  if (person.email)
    parts.push(
      <a key="email" href={"mailto:" + person.email}>
        {person.email}
      </a>,
    );
  if (person.phone)
    parts.push(
      dial ? (
        <a key="phone" href={dial}>
          {person.phone}
        </a>
      ) : (
        <span key="phone">{person.phone}</span>
      ),
    );
  // Addresses and numbers say what they are; a handle and a place get a name for screen readers.
  if (person.chat)
    parts.push(
      <span key="chat">
        <span className="visually-hidden">Chat: </span>
        {person.chat}
      </span>,
    );
  if (person.location)
    parts.push(
      <span key="location">
        <span className="visually-hidden">Location: </span>
        {person.location}
      </span>,
    );
  if (!parts.length) return null;
  return (
    <div
      className="person-contact"
      role="group"
      aria-label={"How to reach " + person.name}
    >
      {parts}
    </div>
  );
}

function PeopleList() {
  const [archived, setArchived] = useState(false);
  const [q, setQ] = useState("");
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [team, setTeam] = useState("");
  const list = useData<List<Person>>(
    `/people?archived=${archived}&q=${encodeURIComponent(q)}`,
  );
  const createPerson = useCommand(commands.createPerson);
  const action = useAction();

  return (
    <>
      <header>
        <h1>People</h1>
        <p className="lede">
          The people you work with, how to reach them, and the work, roadmaps
          and repositories they are tied to.
        </p>
      </header>
      <form
        className="quick-add"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!name.trim()) return;
          await createPerson
            .mutateAsync({
              name: name.trim(),
              title: title.trim(),
              team: team.trim(),
            })
            .then(() => {
              setName("");
              setTitle("");
              setTeam("");
            })
            .catch(() => undefined);
        }}
      >
        <input
          aria-label="Person name"
          placeholder="Add a person…"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={500}
          required
        />
        <input
          aria-label="Person title"
          placeholder="Title (optional)…"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={500}
        />
        <input
          aria-label="Person team"
          placeholder="Team (optional)…"
          value={team}
          onChange={(e) => setTeam(e.target.value)}
          maxLength={500}
        />
        <button
          className="primary"
          disabled={createPerson.isPending || !name.trim()}
        >
          Add person
        </button>
        <ErrorMessage error={createPerson.error} />
      </form>
      <div className="toolbar">
        <input
          aria-label="Filter people by text"
          placeholder="Find people…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <label>
          <input
            type="checkbox"
            checked={archived}
            onChange={(e) => setArchived(e.target.checked)}
          />{" "}
          Show archive
        </label>
      </div>
      <ErrorMessage error={list.error || action.error} />
      {list.isPending ? (
        <p>Loading people…</p>
      ) : !list.data?.items.length ? (
        <Empty>
          {q.trim()
            ? "No one matches this search."
            : archived
              ? "No archived people."
              : "Add the people you work with to tie them to work items, roadmaps and repositories."}
        </Empty>
      ) : (
        <div className="work-list">
          {list.data.items.map((person) => (
            <article className="work-row person-row" key={person.id}>
              <div>
                <a className="item-title" href={"#people/" + person.id}>
                  {person.name}
                </a>
                <small>
                  {[
                    personLine(person.title, person.team),
                    plural(
                      person.workItemCount ?? 0,
                      "work item",
                      "work items",
                    ),
                    plural(person.roadmapCount ?? 0, "roadmap", "roadmaps"),
                    plural(
                      person.repositoryCount ?? 0,
                      "repository",
                      "repositories",
                    ),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </small>
                {person.description && (
                  <p className="person-description" title={person.description}>
                    {person.description}
                  </p>
                )}
                <Contact person={person} />
              </div>
              <button
                className="quiet"
                onClick={() =>
                  action.mutate({
                    path: `/people/${person.id}/${archived ? "recover" : "archive"}`,
                    body: { revision: person.revision },
                  })
                }
              >
                {archived ? "Recover" : "Archive"}
              </button>
            </article>
          ))}
        </div>
      )}
    </>
  );
}

// The person's own fields, each saved on its own as it is typed.
const FIELDS = [
  ["name", "Name", "", 500],
  ["title", "Title", "e.g. Staff Engineer", 500],
  ["team", "Team", "e.g. Platform", 500],
  ["email", "Email", "name@example.com", 320],
  ["phone", "Phone", "+1 312 555 0100", 100],
  ["chat", "Chat", "e.g. @dana on Slack", 500],
  ["location", "Location or time zone", "e.g. Chicago (US Central)", 500],
] as const;

export function PersonDetailView({ id }: { id: string }) {
  const query = useData<PersonDetail>("/people/" + id);
  const action = useAction();

  const person = query.data;
  if (!person)
    return (
      <>
        <ErrorMessage error={query.error} />
        <p>Loading person…</p>
      </>
    );
  const save =
    (field: (typeof FIELDS)[number][0] | "description" | "notes") =>
    (value: string, revision: number) =>
      commands.updatePerson(id, { revision, [field]: value });
  // Picking a match ties it at once; its role is set in its row.
  const tie = (targetType: TargetType, targetId: string) =>
    action.mutate({
      path: "/person-links",
      body: { personId: id, targetType, targetId, relationship: "" },
    });
  const tiedItems = new Set(person.workItems.map((link) => link.itemId));
  const tiedRoadmaps = new Set(person.roadmaps.map((link) => link.roadmapId));
  const tiedRepositories = new Set(
    person.repositories.map((link) => link.repositoryId),
  );

  return (
    <>
      <a className="back" href="#people">
        <Icon name="back" />
        All people
      </a>
      <header className="title-row">
        <div>
          <h1>{person.name}</h1>
          {personLine(person.title, person.team) && (
            <p className="lede">{personLine(person.title, person.team)}</p>
          )}
          <Contact person={person} />
        </div>
        <div className="toolbar">
          <button
            onClick={() =>
              action.mutate({
                path: `/people/${id}/${person.archived ? "recover" : "archive"}`,
                body: { revision: person.revision },
              })
            }
          >
            {person.archived ? "Recover" : "Archive"}
          </button>
        </div>
      </header>
      {person.archived && (
        <p>Archived person. Recover them to tie them to more work.</p>
      )}
      <ErrorMessage error={action.error} />
      <div className="person-fields">
        {FIELDS.map(([field, label, placeholder, maxLength]) => (
          <DraftField
            key={field}
            draftKey={`person-${field}-${id}`}
            label={label}
            initial={person[field]}
            revision={person.revision}
            multiline={false}
            placeholder={placeholder || undefined}
            maxLength={maxLength}
            save={save(field)}
          />
        ))}
      </div>
      <DraftField
        draftKey={"person-description-" + id}
        label="Description"
        initial={person.description}
        revision={person.revision}
        placeholder="Who they are and what to go to them for…"
        maxLength={10000}
        preview={false}
        save={save("description")}
      />
      <DraftField
        draftKey={"person-notes-" + id}
        label="Notes (Markdown)"
        initial={person.notes}
        revision={person.revision}
        save={save("notes")}
      />
      <section>
        <h3>
          Work items <span className="count">{person.workItems.length}</span>
        </h3>
        {person.workItems.map((link) => (
          <div className="line" key={link.linkId}>
            <span className={"status-dot " + link.item.status} />
            <a className="item-title" href={"#work/" + link.itemId}>
              {link.item.title}
            </a>
            <StatusSelect
              label={"Status for " + link.item.title}
              value={link.item.status}
              onChange={(status) =>
                action.mutate({
                  path: "/work-items/" + link.itemId,
                  method: "PATCH",
                  body: { revision: link.item.revision, status },
                })
              }
            />
            <TieRole
              tie={{ personId: id, targetType: "item", targetId: link.itemId }}
              name={"Role on " + link.item.title}
              unlinkName={"Unlink " + link.item.title}
              relationship={link.relationship}
              disabled={person.archived}
              placeholder="Role (e.g. Owner, Reviewer)…"
            />
          </div>
        ))}
        {!person.archived && (
          <div className="person-tie">
            <LinkPicker<Item>
              action="Tie work item"
              label="Work to tie"
              placeholder="Find work…"
              path={(q) =>
                `/work-items?view=all&archived=false&limit=20&q=${encodeURIComponent(q)}`
              }
              linked={(item) => tiedItems.has(item.id)}
              option={(item) => (
                <>
                  <span>{item.title}</span>
                  <small>{statusLabel(item.status)}</small>
                </>
              )}
              none="No work to tie yet."
              onPick={(item) => tie("item", item.id)}
              disabled={action.isPending}
            />
          </div>
        )}
      </section>
      <section>
        <h3>
          Roadmaps <span className="count">{person.roadmaps.length}</span>
        </h3>
        {person.roadmaps.map((link) => (
          <div className="line" key={link.linkId}>
            <a className="item-title" href={"#roadmaps/" + link.roadmapId}>
              {link.roadmap.title}
            </a>
            {link.roadmap.archived && <small>Archived</small>}
            <TieRole
              tie={{
                personId: id,
                targetType: "roadmap",
                targetId: link.roadmapId,
              }}
              name={"Role on " + link.roadmap.title}
              unlinkName={"Unlink " + link.roadmap.title}
              relationship={link.relationship}
              disabled={person.archived}
              placeholder="Role (e.g. Owner, Stakeholder)…"
            />
          </div>
        ))}
        {!person.archived && (
          <div className="person-tie">
            <LinkPicker<Roadmap>
              action="Tie roadmap"
              label="Roadmap to tie"
              placeholder="Find roadmaps…"
              path={(q) =>
                `/roadmaps?archived=false&q=${encodeURIComponent(q)}`
              }
              linked={(roadmap) => tiedRoadmaps.has(roadmap.id)}
              option={(roadmap) => <span>{roadmap.title}</span>}
              none="No roadmaps to tie yet."
              onPick={(roadmap) => tie("roadmap", roadmap.id)}
              disabled={action.isPending}
            />
          </div>
        )}
      </section>
      <section>
        <h3>
          Repositories{" "}
          <span className="count">{person.repositories.length}</span>
        </h3>
        {person.repositories.map((link) => (
          <div className="line" key={link.linkId}>
            <a
              className="item-title"
              href={"#repositories/" + link.repositoryId}
            >
              {link.repository.name}
            </a>
            {link.repository.archived && <small>Archived</small>}
            <TieRole
              tie={{
                personId: id,
                targetType: "repository",
                targetId: link.repositoryId,
              }}
              name={"Role on " + link.repository.name}
              unlinkName={"Unlink " + link.repository.name}
              relationship={link.relationship}
              disabled={person.archived}
              placeholder="Role (e.g. Maintainer, Reviewer)…"
            />
          </div>
        ))}
        {!person.archived && (
          <div className="person-tie">
            <LinkPicker<Repository>
              action="Tie repository"
              label="Repository to tie"
              placeholder="Find repositories…"
              path={(q) =>
                `/repositories?archived=false&limit=20&q=${encodeURIComponent(q)}`
              }
              linked={(repo) => tiedRepositories.has(repo.id)}
              option={(repo) => (
                <>
                  <span>{repo.name}</span>
                  {repo.url && <small>{repo.url}</small>}
                </>
              )}
              none="No repositories to tie yet."
              onPick={(repo) => tie("repository", repo.id)}
              disabled={action.isPending}
            />
          </div>
        )}
      </section>
      <History items={person.history} />
    </>
  );
}
