import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request, getDataContext } from "./api";
import { commands } from "./commands";
import { useCommand, useData, useWrites } from "./hooks";
import { AgentChatsSection } from "./sections/AgentChats";
import { Checklist } from "./sections/Checklist";
import { Files } from "./sections/Files";
import { GithubRefsSection } from "./sections/GithubRefs";
import { History } from "./sections/History";
import { LinksSection } from "./sections/Links";
import { Scratchpad } from "./sections/Notes";
import { PeopleLinks, PersonBadges } from "./sections/PeopleLinks";
import { RecoverRecords } from "./sections/RecoverRecords";
import { DraftField } from "./ui/DraftField";
import { Empty, ErrorMessage } from "./ui/Messages";
import { ITEM_STATUSES, statusLabel, StatusSelect } from "./ui/Status";
import type {
  Answer,
  Detail,
  ItemListed,
  ItemStatus,
  Roadmap,
} from "./apiTypes";
import {
  RepositoryBadges,
  RepositoryPicker,
  RepositoryFilter,
} from "./RepositoryControls";
import { AgentTies } from "./AgentTies";
import { Coordination } from "./Coordination";
import { AppliedProposals } from "./ProposalHistory";
import { RecordReminders } from "./RecordReminders";
import { ReleasesSection } from "./Releases";
import { TicketsSection } from "./Tickets";
import { Icon } from "./icons";
import { RankButton, RankPicker, useRank } from "./StackRank";
export function CreateItem({ day }: { day?: string }) {
  const [title, setTitle] = useState("");
  const [repositoryIds, setRepositoryIds] = useState<string[]>([]);
  const [mode, setMode] = useState("plan");
  const action = useCommand(commands.createItem);
  return (
    <form
      className="quick-add"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!title.trim()) return;
        await action
          .mutateAsync({
            title,
            description: "",
            day,
            worked: mode === "log",
            ...(repositoryIds.length ? { repositoryIds } : {}),
          })
          .then(() => setTitle(""))
          .catch(() => undefined);
      }}
    >
      <input
        aria-label="New work item"
        placeholder={day ? "What matters today?" : "Capture a work item…"}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        maxLength={500}
      />
      {day && (
        <select
          aria-label="Capture mode"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="plan">Plan work</option>
          <option value="log">Log work already performed</option>
        </select>
      )}
      <RepositoryPicker
        label="New work repositories"
        folded
        value={repositoryIds}
        onChange={setRepositoryIds}
        disabled={action.isPending}
      />
      <button className="primary" disabled={action.isPending || !title.trim()}>
        Add work
      </button>
      <ErrorMessage error={action.error} />
    </form>
  );
}
export function Work({ selected }: { selected?: string }) {
  return selected ? <ItemDetail id={selected} /> : <WorkList />;
}
function WorkList() {
  const [archived, setArchived] = useState(false);
  // Any pressed state matches; none pressed shows every state.
  const [statuses, setStatuses] = useState<ItemStatus[]>([]);
  const [repositoryId, setRepositoryId] = useState("");
  const [personId, setPersonId] = useState("");
  const [view, setView] = useState("all");
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const people = useData<Answer<"/api/v1/people">>("/people?archived=false");
  // In a fixed order, so the same states always make the same query.
  const statusQuery = ITEM_STATUSES.filter((s) => statuses.includes(s))
    .map((s) => "&status=" + s)
    .join("");
  const page = (from: number, limit: number) =>
    `/work-items?view=${view}&archived=${archived}&limit=${limit}&offset=${from}&q=${encodeURIComponent(q)}${statusQuery}${personId ? "&personId=" + encodeURIComponent(personId) : ""}${repositoryId ? "&repositoryId=" + encodeURIComponent(repositoryId) : ""}`;
  const list = useData<Answer<"/api/v1/work-items">>(page(offset, 100));
  const action = useWrites();
  const rank = useRank();
  // One row's rank picker is open at a time.
  const [ranking, setRanking] = useState<string | null>(null);
  // Reading the row beside one at a page's edge, before moving past it.
  const [seeking, setSeeking] = useState(false);
  const [seekError, setSeekError] = useState<unknown>(null);
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const rankedCount = list.data?.rankedCount ?? 0;
  // Up and down move past the row shown beside this one, which a filter may leave a few ranks
  // away. At a page's edge that row is on the next or previous page, so it is read first; with
  // no row there at all, the item moves one rank.
  const move = async (index: number, delta: number) => {
    const item = items[index];
    if (!item.rank) return;
    setSeekError(null);
    let beside: ItemListed | undefined = items[index + delta];
    const at = offset + index + delta;
    if (!beside && at >= 0 && at < total) {
      setSeeking(true);
      try {
        beside = (await request<Answer<"/api/v1/work-items">>(page(at, 1)))
          .items[0];
      } catch (error) {
        setSeekError(error);
        return;
      } finally {
        setSeeking(false);
      }
    }
    const target = beside?.rank ?? item.rank + delta;
    if (target < 1 || target > rankedCount) return;
    rank.mutate({ id: item.id, rank: target, revision: item.revision });
  };
  const busy = action.isPending || rank.isPending || seeking || list.isFetching;
  // A sheet: one row per item, one column per fact. From 900px wide it fills the window,
  // and its header row and its rank and title columns stay put while the rows scroll under them.
  return (
    <div className="bounded-screen work-screen">
      <header>
        <h1>Work</h1>
        <p className="lede">
          Capture work once and keep each item’s next action current.
        </p>
      </header>
      <CreateItem />
      <ErrorMessage
        error={list.error || action.error || rank.error || seekError}
      />
      <div className="work-grid">
        {/* Two groups that wrap as wholes: where and who, then which work and in what state. */}
        <div className="toolbar work-filters">
          <div className="work-filters-find">
            <RepositoryFilter
              value={repositoryId}
              onChange={(id) => {
                setRepositoryId(id);
                setOffset(0);
              }}
            />
            <input
              aria-label="Filter work by text"
              placeholder="Find in work…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOffset(0);
              }}
            />
            <select
              aria-label="Filter by person"
              value={personId}
              onChange={(e) => {
                setPersonId(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">All people</option>
              {people.data?.items?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <div className="work-filters-state">
            <select
              aria-label="Work view"
              value={view}
              onChange={(e) => {
                setView(e.target.value);
                setOffset(0);
              }}
            >
              <option value="all">All work</option>
              <option value="backlog">Unscheduled backlog</option>
            </select>
            <label>
              <input
                type="checkbox"
                checked={archived}
                onChange={(e) => {
                  setArchived(e.target.checked);
                  setOffset(0);
                }}
              />{" "}
              Show archive
            </label>
            <div
              className="status-toggles"
              role="group"
              aria-label="Filter status"
            >
              {ITEM_STATUSES.map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={statuses.includes(s)}
                  onClick={() => {
                    setStatuses(
                      statuses.includes(s)
                        ? statuses.filter((other) => other !== s)
                        : [...statuses, s],
                    );
                    setOffset(0);
                  }}
                >
                  <span className={"status-dot " + s} aria-hidden="true" />
                  {statusLabel(s)}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="work-grid-body">
          <table aria-label="Work items">
            <colgroup>
              <col className="col-rank" />
              <col className="col-title" />
              <col className="col-status" />
              <col />
              <col className="col-people" />
              <col className="col-repositories" />
              <col className="col-order" />
              <col className="col-archive" />
            </colgroup>
            <thead>
              <tr>
                <th scope="col" className="cell-rank">
                  Rank
                </th>
                <th scope="col" className="cell-title">
                  Title
                </th>
                <th scope="col">Status</th>
                <th scope="col">Description</th>
                <th scope="col">People</th>
                <th scope="col">Repositories</th>
                <th scope="col">Order</th>
                {/* The column of Archive (or Recover) buttons is named for screen readers; its buttons say it. */}
                <th scope="col" aria-label={archived ? "Recover" : "Archive"} />
              </tr>
            </thead>
            <tbody>
              {list.isPending ? (
                <tr>
                  <td colSpan={8}>Loading work…</td>
                </tr>
              ) : !items.length ? (
                <tr>
                  <td colSpan={8}>
                    <Empty>No work items yet. Add your first one above.</Empty>
                  </td>
                </tr>
              ) : (
                items.map((item, index) => (
                  <Fragment key={item.id}>
                    <tr className="work-row">
                      <td className="cell-rank">
                        <RankButton
                          item={item}
                          open={ranking === item.id}
                          disabled={busy}
                          onToggle={() =>
                            setRanking(ranking === item.id ? null : item.id)
                          }
                        />
                      </td>
                      <th scope="row" className="cell-title">
                        <a
                          className="item-title"
                          href={"#work/" + item.id}
                          title={item.title}
                        >
                          {item.title}
                        </a>
                      </th>
                      <td>
                        <StatusSelect
                          value={item.status}
                          options={ITEM_STATUSES}
                          onChange={(status) =>
                            action.mutate(() =>
                              commands.updateItem(item.id, {
                                revision: item.revision,
                                status,
                              }),
                            )
                          }
                        />
                      </td>
                      <td className="cell-description">
                        <span
                          className={item.description ? undefined : "muted"}
                        >
                          {item.description?.slice(0, 1000) ||
                            "Ready for a next step"}
                        </span>
                      </td>
                      <td>
                        <PersonBadges people={item.people} />
                      </td>
                      <td className="cell-repositories">
                        <RepositoryBadges ids={item.repositoryIds} />
                      </td>
                      <td>
                        <div className="reorder">
                          <button
                            aria-label={"Move " + item.title + " up"}
                            onClick={() => void move(index, -1)}
                            disabled={!item.rank || item.rank === 1 || busy}
                          >
                            <Icon name="up" />
                          </button>
                          <button
                            aria-label={"Move " + item.title + " down"}
                            onClick={() => void move(index, 1)}
                            disabled={
                              !item.rank || item.rank >= rankedCount || busy
                            }
                          >
                            <Icon name="down" />
                          </button>
                        </div>
                      </td>
                      <td>
                        <button
                          className="quiet"
                          onClick={() =>
                            action.mutate(() =>
                              commands.setItemArchived(item.id, !archived, {
                                revision: item.revision,
                              }),
                            )
                          }
                        >
                          {archived ? "Recover" : "Archive"}
                        </button>
                      </td>
                    </tr>
                    {/* An open rank picker gets a row of its own under its item, so no column narrows it. */}
                    {ranking === item.id && item.rank && (
                      <tr className="rank-row">
                        <td colSpan={8}>
                          <RankPicker
                            item={item}
                            rankedCount={rankedCount}
                            onClose={() => setRanking(null)}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="work-grid-foot">
          <span>
            {list.data &&
              (total > 100
                ? `${offset + 1} to ${Math.min(offset + 100, total)} of ${total}`
                : `${total} work ${total === 1 ? "item" : "items"}`)}
          </span>
          {total > 100 && (
            <div className="work-pager">
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 100))}
              >
                Previous work
              </button>
              <button
                disabled={offset + 100 >= total}
                onClick={() => setOffset(offset + 100)}
              >
                Next work
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
export function ItemDetail({ id }: { id: string }) {
  const query = useData<Detail>("/work-items/" + id);
  const roads = useData<Answer<"/api/v1/roadmaps">>("/roadmaps?archived=false");
  const [selectedRoad, setSelectedRoad] = useState<string | null>(null);
  const item = query.data;
  const activeRoadId =
    selectedRoad ?? item?.roadmapId ?? item?.linkedStep?.roadmapId ?? "";
  const selectedRoadmap = useQuery({
    queryKey: [getDataContext(), "/roadmaps/" + activeRoadId],
    queryFn: () => request<Roadmap>("/roadmaps/" + activeRoadId),
    enabled: !!activeRoadId,
  });
  const action = useWrites();
  const [ranking, setRanking] = useState(false);
  const [date, setDate] = useState("");
  if (!item)
    return (
      <>
        <ErrorMessage error={query.error} />
        <p>Loading work item…</p>
      </>
    );
  const patch = (fields: Record<string, unknown>) =>
    action.mutate(() =>
      commands.updateItem(id, { revision: item.revision, ...fields }),
    );
  return (
    <>
      <a className="back" href="#work">
        <Icon name="back" />
        All work
      </a>
      <header>
        <h1>{item.title}</h1>
        <div className="toolbar">
          <RankButton
            item={item}
            open={ranking}
            onToggle={() => setRanking(!ranking)}
            suffix={"of " + item.rankedCount}
          />
          <StatusSelect
            value={item.status}
            options={ITEM_STATUSES}
            onChange={(status) => patch({ status })}
          />
          <button
            onClick={() =>
              action.mutate(() =>
                commands.setItemArchived(id, !item.archived, {
                  revision: item.revision,
                }),
              )
            }
          >
            {item.archived ? "Recover" : "Archive"}
          </button>
        </div>
        {ranking && item.rank && (
          <RankPicker
            item={item}
            rankedCount={item.rankedCount}
            onClose={() => setRanking(false)}
          />
        )}
      </header>
      <ErrorMessage error={action.error} />
      <DraftField
        draftKey={"item-title-" + id}
        label="Title"
        initial={item.title}
        revision={item.revision}
        multiline={false}
        save={(title, revision) => commands.updateItem(id, { revision, title })}
      />
      <DraftField
        draftKey={"item-description-" + id}
        label="Description (Markdown)"
        initial={item.description}
        revision={item.revision}
        save={(description, revision) =>
          commands.updateItem(id, { revision, description })
        }
      />
      <div className="two-column">
        <section>
          <h3>Context</h3>
          <DraftField
            draftKey={"item-ado-" + id}
            label="Azure DevOps link"
            initial={item.adoUrl ?? ""}
            revision={item.revision}
            multiline={false}
            placeholder="https://dev.azure.com/…"
            save={(link, revision) =>
              commands.updateItem(id, { revision, adoUrl: link.trim() || null })
            }
          />
          {item.adoUrl && /^https?:\/\//i.test(item.adoUrl) && (
            <a href={item.adoUrl} target="_blank" rel="noreferrer">
              Open work item ↗
            </a>
          )}
          <RepositoryPicker
            value={item.repositoryIds ?? []}
            onChange={(repositoryIds) => patch({ repositoryIds })}
            disabled={!!item.archived || action.isPending}
          />
          <label>
            Roadmap
            <select
              value={activeRoadId}
              onChange={(e) => {
                const nextRoad = e.target.value;
                setSelectedRoad(nextRoad);
                patch({ roadmapId: nextRoad || null, stepId: null });
              }}
            >
              <option value="">No linked roadmap</option>
              {activeRoadId &&
                !roads.data?.items.some((r) => r.id === activeRoadId) && (
                  <option value={activeRoadId}>
                    {item.linkedRoadmap?.title ??
                      item.linkedStep?.roadmapTitle ??
                      "Current linked roadmap"}
                  </option>
                )}
              {roads.data?.items.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.title}
                </option>
              ))}
            </select>
          </label>
          {item.linkedStep ? (
            <p>
              <a href={"#roadmaps/" + item.linkedStep.roadmapId}>
                {item.linkedStep.roadmapTitle} / {item.linkedStep.title}
              </a>
            </p>
          ) : (
            item.linkedRoadmap && (
              <p>
                <a href={"#roadmaps/" + item.linkedRoadmap.id}>
                  {item.linkedRoadmap.title}
                </a>
              </p>
            )
          )}
          <label>
            Roadmap step
            <select
              value={item.stepId ?? ""}
              onChange={(e) =>
                patch({
                  ...(activeRoadId ? { roadmapId: activeRoadId } : {}),
                  stepId: e.target.value || null,
                })
              }
            >
              <option value="">No linked step</option>
              {item.stepId &&
                !selectedRoadmap.data?.steps.some(
                  (s) => s.id === item.stepId,
                ) && (
                  <option value={item.stepId}>
                    {item.linkedStep?.title ?? "Current linked step"}
                  </option>
                )}
              {selectedRoadmap.data?.steps
                ?.filter((s) => !s.retired)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
            </select>
          </label>
          <form
            className="inline"
            onSubmit={(e) => {
              e.preventDefault();
              action.mutate(() =>
                commands.upsertEntry(date, id, { revision: 0, planned: true }),
              );
            }}
          >
            <input
              aria-label="Schedule date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              required
            />
            <button>Plan for day</button>
          </form>
        </section>
        <Checklist itemId={id} tasks={item.tasks ?? []} />
      </div>
      <Coordination key={id} itemId={id} />
      <Scratchpad itemId={id} notes={item.notes} />
      <PeopleLinks targetType="item" targetId={id} people={item.people} />
      <AgentTies targetType="item" targetId={id} />
      <LinksSection ownerType="item" ownerId={id} links={item.links} />
      <AgentChatsSection ownerType="item" ownerId={id} chats={item.chats} />
      <GithubRefsSection
        ownerType="item"
        ownerId={id}
        github={item.github}
        chats={item.chats}
      />
      <TicketsSection itemId={id} disabled={!!item.archived} />
      <ReleasesSection
        targetType="item"
        targetId={id}
        disabled={!!item.archived}
      />
      {!!item.dailyEntries?.length && (
        <section>
          <h3>Daily record</h3>
          {item.dailyEntries.map((entry) => (
            <div className="line" key={entry.id}>
              <a className="line-date" href={"#today/" + entry.date}>
                {entry.date}
              </a>
              <small>
                {[entry.planned && "Planned", entry.worked && "Worked"]
                  .filter(Boolean)
                  .join(", ")}
              </small>
              <span>{entry.outcome || "No outcome recorded"}</span>
            </div>
          ))}
        </section>
      )}
      <Files
        ownerType="item"
        ownerId={id}
        files={item.attachments}
        references={item.references ?? []}
      />
      <RecoverRecords
        groups={[
          // Archived notes are read under the scratchpad: beside it, one could only be refused.
          { label: "Tasks", path: "tasks", records: item.tasks },
          { label: "Files", path: "attachments", records: item.attachments },
          { label: "Paths", path: "references", records: item.references },
          { label: "Links", path: "links", records: item.links ?? [] },
          {
            label: "Agent chats",
            path: "chats",
            records: item.chats ?? [],
          },
          {
            label: "GitHub",
            path: "github",
            records: item.github ?? [],
          },
        ]}
      />
      <RecordReminders
        targetType="item"
        targetId={id}
        archived={!!item.archived}
      />
      <AppliedProposals path={"/work-items/" + id} noun="work item" />
      <History items={item.history} />
    </>
  );
}
