import { useState } from "react";
import { commands } from "./commands";
import { useAction, useCommand, useData } from "./hooks";
import {
  DraftField,
  Empty,
  ErrorMessage,
  History,
  StatusSelect,
} from "./components";
import type { Item, List, Person, PersonDetail, Roadmap } from "./types";

export function People({ selected }: { selected?: string }) {
  return selected ? <PersonDetailView id={selected} /> : <PeopleList />;
}

function PeopleList() {
  const [archived, setArchived] = useState(false);
  const [q, setQ] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const list = useData<List<Person>>(
    `/people?archived=${archived}&q=${encodeURIComponent(q)}`,
  );
  const createPerson = useCommand(commands.createPerson);
  const action = useAction();

  return (
    <>
      <header>
        <p className="eyebrow">Who you work with</p>
        <h1>People</h1>
        <p className="lede">
          Track owners, collaborators, and stakeholders across roadmaps and work
          items.
        </p>
      </header>
      <form
        className="quick-add"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!name.trim()) return;
          await createPerson
            .mutateAsync({ name: name.trim(), role: role.trim(), notes: "" })
            .then(() => {
              setName("");
              setRole("");
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
          aria-label="Person title or team"
          placeholder="Title or team (optional)…"
          value={role}
          onChange={(e) => setRole(e.target.value)}
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
          Add collaborators or stakeholders to tie them to roadmaps and work
          items.
        </Empty>
      ) : (
        <div className="work-list">
          {list.data.items.map((person) => (
            <article className="work-row" key={person.id}>
              <div>
                <a className="item-title" href={"#people/" + person.id}>
                  {person.name}
                </a>
                <small>
                  {person.role ? `${person.role} · ` : ""}
                  {person.roadmapCount ?? 0} roadmaps ·{" "}
                  {person.workItemCount ?? 0} work items
                </small>
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

export function PersonDetailView({ id }: { id: string }) {
  const query = useData<PersonDetail>("/people/" + id);
  const roadmapsList = useData<List<Roadmap>>("/roadmaps?archived=false");
  const workItemsList = useData<List<Item>>(
    "/work-items?view=all&archived=false&limit=100",
  );
  const action = useAction();
  const [pickRoadmapId, setPickRoadmapId] = useState("");
  const [roadmapRole, setRoadmapRole] = useState("");
  const [pickItemId, setPickItemId] = useState("");
  const [itemRole, setItemRole] = useState("");

  const person = query.data;
  if (!person)
    return (
      <>
        <ErrorMessage error={query.error} />
        <p>Loading person…</p>
      </>
    );

  return (
    <>
      <a className="back" href="#people">
        ← All people
      </a>
      <header>
        <p className="eyebrow">Person</p>
        <h1>{person.name}</h1>
        {person.role && <p className="lede">{person.role}</p>}
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
      <ErrorMessage error={action.error} />
      <DraftField
        draftKey={"person-name-" + id}
        label="Name"
        initial={person.name}
        revision={person.revision}
        multiline={false}
        save={(name, revision) => commands.updatePerson(id, { revision, name })}
      />
      <DraftField
        draftKey={"person-role-" + id}
        label="Title or team"
        initial={person.role}
        revision={person.revision}
        multiline={false}
        placeholder="e.g. Staff Engineer · Platform"
        save={(role, revision) => commands.updatePerson(id, { revision, role })}
      />
      <DraftField
        draftKey={"person-notes-" + id}
        label="Notes · Markdown"
        initial={person.notes}
        revision={person.revision}
        save={(notes, revision) =>
          commands.updatePerson(id, { revision, notes })
        }
      />
      <section>
        <h3>
          Roadmaps <span className="count">{person.roadmaps.length}</span>
        </h3>
        {person.roadmaps.map((link) => (
          <div className="line" key={link.linkId}>
            <a className="item-title" href={"#roadmaps/" + link.roadmapId}>
              {link.roadmap.title}
            </a>
            <input
              aria-label={"Role on " + link.roadmap.title}
              defaultValue={link.relationship}
              key={link.linkId + ":" + link.relationship}
              disabled={person.archived}
              placeholder="Role (e.g. Owner, Stakeholder)…"
              onBlur={(e) => {
                const next = e.target.value.trim();
                if (next !== link.relationship) {
                  action.mutate({
                    path: "/person-links",
                    body: {
                      personId: id,
                      targetType: "roadmap",
                      targetId: link.roadmapId,
                      relationship: next,
                    },
                  });
                }
              }}
            />
            <button
              type="button"
              className="quiet"
              aria-label={"Unlink " + link.roadmap.title}
              onClick={() =>
                action.mutate({
                  path: "/person-links/unlink",
                  body: {
                    personId: id,
                    targetType: "roadmap",
                    targetId: link.roadmapId,
                  },
                })
              }
            >
              Unlink
            </button>
          </div>
        ))}
        {!person.archived && (
          <form
            className="inline"
            onSubmit={async (e) => {
              e.preventDefault();
              if (!pickRoadmapId) return;
              await action
                .mutateAsync({
                  path: "/person-links",
                  body: {
                    personId: id,
                    targetType: "roadmap",
                    targetId: pickRoadmapId,
                    relationship: roadmapRole.trim(),
                  },
                })
                .then(() => {
                  setPickRoadmapId("");
                  setRoadmapRole("");
                })
                .catch(() => undefined);
            }}
          >
            <select
              aria-label="Choose roadmap to tie"
              value={pickRoadmapId}
              onChange={(e) => setPickRoadmapId(e.target.value)}
            >
              <option value="">Tie a roadmap…</option>
              {roadmapsList.data?.items?.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.title}
                </option>
              ))}
            </select>
            <input
              aria-label="Roadmap relationship or role"
              placeholder="Role (e.g. Owner, Stakeholder)…"
              value={roadmapRole}
              onChange={(e) => setRoadmapRole(e.target.value)}
            />
            <button
              type="submit"
              disabled={!pickRoadmapId || action.isPending}
            >
              Tie roadmap
            </button>
          </form>
        )}
      </section>
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
            <input
              aria-label={"Role on " + link.item.title}
              defaultValue={link.relationship}
              key={link.linkId + ":" + link.relationship}
              disabled={person.archived}
              placeholder="Role (e.g. Owner, Reviewer)…"
              onBlur={(e) => {
                const next = e.target.value.trim();
                if (next !== link.relationship) {
                  action.mutate({
                    path: "/person-links",
                    body: {
                      personId: id,
                      targetType: "item",
                      targetId: link.itemId,
                      relationship: next,
                    },
                  });
                }
              }}
            />
            <button
              type="button"
              className="quiet"
              aria-label={"Unlink " + link.item.title}
              onClick={() =>
                action.mutate({
                  path: "/person-links/unlink",
                  body: {
                    personId: id,
                    targetType: "item",
                    targetId: link.itemId,
                  },
                })
              }
            >
              Unlink
            </button>
          </div>
        ))}
        {!person.archived && (
          <form
            className="inline"
            onSubmit={async (e) => {
              e.preventDefault();
              if (!pickItemId) return;
              await action
                .mutateAsync({
                  path: "/person-links",
                  body: {
                    personId: id,
                    targetType: "item",
                    targetId: pickItemId,
                    relationship: itemRole.trim(),
                  },
                })
                .then(() => {
                  setPickItemId("");
                  setItemRole("");
                })
                .catch(() => undefined);
            }}
          >
            <select
              aria-label="Choose work item to tie"
              value={pickItemId}
              onChange={(e) => setPickItemId(e.target.value)}
            >
              <option value="">Tie a work item…</option>
              {workItemsList.data?.items?.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.title}
                </option>
              ))}
            </select>
            <input
              aria-label="Work item relationship or role"
              placeholder="Role (e.g. Owner, Reviewer)…"
              value={itemRole}
              onChange={(e) => setItemRole(e.target.value)}
            />
            <button type="submit" disabled={!pickItemId || action.isPending}>
              Tie work item
            </button>
          </form>
        )}
      </section>
      <History items={person.history} />
    </>
  );
}
