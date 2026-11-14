import { ChatHistory, ChatListItem, useChatList } from "./Chats";
import { commands } from "./commands";
import { useWrites } from "./hooks";
import { ChatForm, TrackedChatActions } from "./sections/AgentChats";
import { RecoverRecords } from "./sections/RecoverRecords";
import { Empty, ErrorMessage } from "./ui/Messages";
import type { ChatOverview, Roadmap } from "./apiTypes";

/** The roadmap's "Chats" view, and a chat opened in it. */
const chatsAddress = (roadmapId: string, chatId?: string) =>
  `#roadmaps/${roadmapId}/chats` + (chatId ? "/" + chatId : "");

/** A chat whose record sits within the roadmap: on it, on its work items, or on its sub-roadmaps and theirs. */
function withinRoadmap(chat: ChatOverview, roadmapId: string) {
  return (
    (chat.ownerType === "roadmap" && chat.ownerId === roadmapId) ||
    chat.roadmaps.some((r) => r.id === roadmapId)
  );
}

/**
 * A roadmap's "Chats": the chats tracked on it, its work items and its sub-roadmaps, working ones
 * first, with the chosen chat's transcript beside them, as on Chats. The roadmap's own chats are
 * tracked, edited and archived here; a work item's or a sub-roadmap's, on that record's page.
 */
export function RoadmapChats({
  road,
  chat: selected,
}: {
  road: Roadmap;
  // The chat the address opens; otherwise the most recently active one.
  chat?: string;
}) {
  const { list, chats: every } = useChatList();
  const action = useWrites();
  const own = (c: ChatOverview) =>
    c.ownerType === "roadmap" && c.ownerId === road.id;
  const chats = every.filter((c) => withinRoadmap(c, road.id));
  const current =
    chats.find((c) => c.id === selected) ?? (selected ? undefined : chats[0]);
  return (
    <div className="split chats-split">
      <div className="panel" role="region" aria-label="Agent chats">
        <div className="panel-head">
          <h2>
            Agent chats <span className="count">{chats.length}</span>
          </h2>
        </div>
        <div className="panel-body">
          <ErrorMessage error={list.error} />
          {list.isPending ? (
            <p>Loading chats…</p>
          ) : !chats.length ? (
            <Empty>
              No chat is tracked on this roadmap, its work items or its
              sub-roadmaps yet.
            </Empty>
          ) : (
            <ul className="chat-list">
              {chats.map((c) => (
                <ChatListItem
                  key={c.id}
                  chat={c}
                  current={c.id === current?.id}
                  ownerShown={own(c)}
                  href={chatsAddress(road.id, c.id)}
                />
              ))}
            </ul>
          )}
          {!road.archived && (
            <section>
              <h3>Track a chat</h3>
              <ChatForm
                label="Agent chat"
                submit="Add chat"
                chats={chats}
                disabled={action.isPending}
                onSave={(fields) =>
                  action
                    .mutateAsync(() =>
                      commands.createChat({
                        ownerType: "roadmap",
                        ownerId: road.id,
                        ...fields,
                      }),
                    )
                    .then((created) => {
                      location.hash = chatsAddress(road.id, created.id);
                    })
                }
              />
              <ErrorMessage error={action.error} />
            </section>
          )}
          <RecoverRecords
            groups={[
              {
                label: "Agent chats",
                path: "chats",
                records: road.chats ?? [],
              },
            ]}
          />
        </div>
      </div>
      <div className="pane detail" role="region" aria-label="Chat history">
        {current ? (
          <ChatHistory
            key={current.id}
            chat={current}
            actions={
              own(current) ? (
                <TrackedChatActions
                  chat={current}
                  chats={chats}
                  disabled={road.archived}
                  onArchived={() => {
                    location.hash = chatsAddress(road.id);
                  }}
                />
              ) : undefined
            }
          />
        ) : selected && list.data ? (
          <Empty>
            This chat is not among this roadmap's chats. It may be archived, or
            tracked on a record outside this roadmap.
          </Empty>
        ) : null}
      </div>
    </div>
  );
}
