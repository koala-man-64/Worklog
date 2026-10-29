import { useData } from "./hooks";
import type { List, Roadmap } from "./types";

// Shares the Roadmaps list query, so renames and archives refresh the nav too.
export function RoadmapNav({ selected }: { selected?: string }) {
  const roads = useData<List<Roadmap>>("/roadmaps?archived=false");
  const items = roads.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <ul className="subnav" aria-label="Roadmaps">
      {items.map((r) => (
        <li key={r.id}>
          <a
            href={"#roadmaps/" + r.id}
            title={r.title || "Untitled"}
            aria-current={selected === r.id ? "page" : undefined}
          >
            {r.title || "Untitled"}
          </a>
        </li>
      ))}
    </ul>
  );
}
