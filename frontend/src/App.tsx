import { useEffect, useState } from "react";
import { localStore } from "./storage";
import { useQuery } from "@tanstack/react-query";
import { bootstrap } from "./api";
import { useData } from "./hooks";
import { ErrorMessage } from "./components";
import { Today } from "./Today";
import { Work } from "./Work";
import { Roadmaps } from "./Roadmaps";
import { Review } from "./Review";
import { Settings } from "./Settings";
import type { List } from "./types";
type Result = {
  id: string;
  type: string;
  title: string;
  snippet: string;
  ownerId?: string;
  routeType?: string;
  routeId?: string;
};
export default function App() {
  const [route, setRoute] = useState(location.hash.slice(1) || "today");
  const [search, setSearch] = useState("");
  const session = useQuery({
    queryKey: ["session"],
    queryFn: bootstrap,
    retry: false,
    staleTime: Infinity,
  });
  useEffect(() => {
    const change = () => {
      setRoute(location.hash.slice(1) || "today");
      setSearch("");
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  const [page, id] = route.split("/");
  useEffect(() => {
    document.documentElement.dataset.theme =
      localStore.read("theme").value ?? "system";
  }, []);
  return (
    <div className="app">
      <a
        className="skip-link"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
          document.getElementById("main")?.scrollIntoView({ block: "start" });
        }}
      >
        Skip to content
      </a>
      <aside className="sidebar">
        <a className="brand" href="#today">
          <span className="brand-mark">w.</span>
          <span>
            worklog<small>A clear record of progress</small>
          </span>
        </a>
        <nav aria-label="Main navigation">
          {[
            ["today", "◉", "Today"],
            ["work", "☷", "Work"],
            ["roadmaps", "◇", "Roadmaps"],
            ["review", "✓", "Review"],
            ["settings", "⚙", "Settings"],
          ].map(([key, icon, label]) => (
            <a
              key={key}
              href={"#" + key}
              aria-current={page === key ? "page" : undefined}
            >
              <span aria-hidden="true">{icon}</span>
              {label}
            </a>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="live-dot" /> On this machine
          <br />
          <small>
            Private by default.
            <br />
            Progress, at your pace.
          </small>
        </div>
      </aside>
      <div className="workspace">
        <div className="topbar">
          <span>
            {session.data?.today ?? "Worklog"}{" "}
            <span className="muted">/ {page}</span>
          </span>
          <label className="search">
            <span aria-hidden="true">⌕</span>
            <input
              aria-label="Search all work"
              placeholder="Search your work…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              type="search"
            />
          </label>
        </div>
        <main id="main" tabIndex={-1} key={session.data?.dataId}>
          {session.isPending ? (
            <p>Opening your worklog…</p>
          ) : session.error ? (
            <div>
              <ErrorMessage error={session.error} />
              <button onClick={() => session.refetch()}>Reconnect</button>
            </div>
          ) : search ? (
            <Search query={search} />
          ) : page === "today" ? (
            <Today
              key={id ?? "today"}
              today={session.data!.today}
              initialDate={id}
            />
          ) : page === "work" ? (
            <Work key={id ?? "list"} selected={id} />
          ) : page === "roadmaps" ? (
            <Roadmaps key={id ?? "list"} selected={id} />
          ) : page === "review" ? (
            <Review key={id ?? "list"} selectedId={id} />
          ) : page === "settings" ? (
            <Settings />
          ) : (
            <>
              <h1>Page not found</h1>
              <a href="#today">Return to today</a>
            </>
          )}
        </main>
        <footer>Small steps. A durable record.</footer>
      </div>
    </div>
  );
}
function Search({ query }: { query: string }) {
  const [stable, setStable] = useState(query);
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const timer = setTimeout(() => {
      setStable(query);
      setOffset(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);
  const results = useData<List<Result>>(
    `/search?q=${encodeURIComponent(stable)}&archived=${archived}&limit=50&offset=${offset}`,
  );
  return (
    <>
      <header>
        <p className="eyebrow">Across your worklog</p>
        <h1>Search</h1>
      </header>
      <label>
        <input
          type="checkbox"
          checked={archived}
          onChange={(e) => {
            setArchived(e.target.checked);
            setOffset(0);
          }}
        />{" "}
        Search archived records
      </label>
      <ErrorMessage error={results.error} />
      <p className="muted">
        {results.data?.total ?? 0} results for “{stable}”
      </p>
      {results.data?.items.map((r) => (
        <article className="search-result" key={r.type + r.id}>
          <small>{r.type}</small>
          <a
            className="item-title"
            href={
              r.routeType && r.routeId
                ? "#" + r.routeType + "/" + r.routeId
                : r.type === "roadmap"
                  ? "#roadmaps/" + r.id
                  : r.type === "proposal"
                    ? "#review/" + r.id
                    : "#work/" + (r.ownerId ?? r.id)
            }
          >
            {r.title || "Untitled"}
          </a>
          <p>{r.snippet}</p>
        </article>
      ))}
      {(results.data?.total ?? 0) > 50 && (
        <div className="toolbar">
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous results
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 50, results.data?.total ?? 0)}
          </span>
          <button
            disabled={offset + 50 >= (results.data?.total ?? 0)}
            onClick={() => setOffset(offset + 50)}
          >
            Next results
          </button>
        </div>
      )}
    </>
  );
}
