import { commands, type ArchiveKind } from "../commands";
import { useWrites } from "../hooks";
import type {
  AgentChat,
  Attachment,
  GithubRef,
  Note,
  WebLink,
} from "../apiTypes";
import { ErrorMessage } from "../ui/Messages";
export function RecoverRecords({
  groups,
}: {
  groups: {
    label: string;
    path: ArchiveKind;
    records: (
      | Attachment
      | Note
      | WebLink
      | AgentChat
      | GithubRef
      | {
          id: string;
          revision: number;
          archived?: boolean;
          text?: string;
          path?: string;
          url?: string;
          title?: string;
          reference?: string;
        }
    )[];
  }[];
}) {
  const action = useWrites();
  const groupsWithRecords = groups
    .map((g) => ({ ...g, records: g.records?.filter((r) => r.archived) ?? [] }))
    .filter((g) => g.records.length);
  if (!groupsWithRecords.length) return null;
  return (
    <details>
      <summary>
        Archived supporting records (
        {groupsWithRecords.reduce((n, g) => n + g.records.length, 0)})
      </summary>
      {groupsWithRecords.map((g) => (
        <section key={g.path}>
          <h3>{g.label}</h3>
          {g.records.map((r) => (
            <div className="line" key={r.id}>
              <span>
                {"name" in r
                  ? r.name
                  : "title" in r && r.title
                    ? r.title
                    : "reference" in r && r.reference
                      ? r.reference
                      : "url" in r && r.url
                        ? r.url
                        : "text" in r && r.text
                          ? r.text
                          : "path" in r && r.path
                            ? r.path
                            : r.id}
              </span>
              <button
                disabled={action.isPending}
                onClick={() =>
                  action.mutate(() =>
                    commands.setArchived(g.path, r.id, false, {
                      revision: r.revision,
                    }),
                  )
                }
              >
                Recover
              </button>
            </div>
          ))}
        </section>
      ))}
      <ErrorMessage error={action.error} />
    </details>
  );
}
