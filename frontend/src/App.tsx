import { Fragment, useEffect, useState } from "react";
import { localStore } from "./storage";
import { useQuery } from "@tanstack/react-query";
import { bootstrap } from "./api";
import { useData } from "./hooks";
import { ErrorMessage } from "./components";
import { Today } from "./Today";
import { Work } from "./Work";
import { Roadmaps } from "./Roadmaps";
import { RoadmapNav } from "./RoadmapNav";
import { People } from "./People";
import { Review } from "./Review";
import { Settings } from "./Settings";
import { Icon, type IconName } from "./icons";
import type { List } from "./types";
const destinations: [IconName, string][] = [
  ["today", "Today"],
  ["work", "Work"],
  ["roadmaps", "Roadmaps"],
  ["people", "People"],
  ["review", "Review"],
  ["settings", "Settings"],
];
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
  const [roadmapsOpen, setRoadmapsOpen] = useState(
    () => localStore.read("nav-roadmaps").value !== "closed",
  );
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
  const [page, id, sub] = route.split("/");
  const link = (key: IconName, label: string) => (
    <a
      href={"#" + key}
      aria-current={
        page !== key ? undefined : key === "roadmaps" && id ? "true" : "page"
      }
    >
      <Icon name={key} size={18} />
      {label}
    </a>
  );
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
          {destinations.map(([key, label]) => (
            <Fragment key={key}>
              {key === "roadmaps" ? (
                <div className="nav-row">
                  {link(key, label)}
                  <button
                    type="button"
                    className="nav-toggle"
                    aria-expanded={roadmapsOpen}
                    aria-controls="roadmap-subnav"
                    aria-label={
                      roadmapsOpen ? "Collapse roadmaps" : "Expand roadmaps"
                    }
                    onClick={() => {
                      localStore.write(
                        "nav-roadmaps",
                        roadmapsOpen ? "closed" : "open",
                      );
                      setRoadmapsOpen(!roadmapsOpen);
                    }}
                  >
                    <Icon
                      name={roadmapsOpen ? "collapse" : "expand"}
                      size={14}
                    />
                  </button>
                </div>
              ) : (
                link(key, label)
              )}
              {key === "roadmaps" && session.data && (
                <RoadmapNav
                  open={roadmapsOpen}
                  selected={page === "roadmaps" ? id : undefined}
                />
              )}
            </Fragment>
          ))}
        </nav>
        <div className="sidebar-note">On this machine. Private by default.</div>
      </aside>
      <div className="workspace">
        <div className="topbar">
          <span>
            {page === "today" && id ? id : (session.data?.today ?? "Worklog")}{" "}
            <span className="muted">/ {page}</span>
          </span>
          <label className="search">
            <Icon name="search" size={18} />
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
            <Roadmaps key={id ?? "list"} selected={id} step={sub} />
          ) : page === "people" ? (
            <People key={id ?? "list"} selected={id} />
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
                  : r.type === "person"
                    ? "#people/" + r.id
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
            {offset + 1} to {Math.min(offset + 50, results.data?.total ?? 0)}
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
