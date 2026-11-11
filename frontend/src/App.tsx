import {
  Fragment,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { localStore } from "./storage";
import { useQuery } from "@tanstack/react-query";
import { bootstrap } from "./api";
import { useData, useStoredWidth } from "./hooks";
import { ErrorMessage } from "./ui/Messages";
import { ResizeHandle } from "./ui/ResizeHandle";
import { Pager } from "./ui/Pager";
import { Today } from "./Today";
import { Work } from "./Work";
import { Roadmaps } from "./Roadmaps";
import { RoadmapNav } from "./RoadmapNav";
import { Releases } from "./Releases";
import { Repositories } from "./Repositories";
import { Tickets } from "./Tickets";
import { People } from "./People";
import { Resources } from "./Resources";
import { Agents } from "./Agents";
import { Chats } from "./Chats";
import { Discussions } from "./Discussions";
import { Review } from "./Review";
import { Settings } from "./Settings";
import { Usage } from "./UsagePage";
import { Guide, guideRoutes } from "./Guide";
import { Icon, type IconName } from "./icons";
import { sections } from "./navigation";
import type { Answer } from "./apiTypes";
import { countPageView } from "./usage";
// The narrowest sidebar still fits the brand; the widest leaves the workspace room.
const SIDEBAR_MIN = 190;
const SIDEBAR_MAX = 420;
export default function App() {
  const [route, setRoute] = useState(location.hash.slice(1) || "today");
  const [search, setSearch] = useState("");
  const [roadmapsOpen, setRoadmapsOpen] = useState(
    () => localStore.read("nav-roadmaps").value !== "closed",
  );
  const sidebar = useStoredWidth("sidebar-width", SIDEBAR_MIN, SIDEBAR_MAX);
  const sidebarRef = useRef<HTMLElement>(null);
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
  // A result for the page already open changes no hash, so choosing it clears search here.
  const chooseResult = () => {
    setSearch("");
    window.scrollTo(0, 0);
  };
  const [page, id, sub] = route.split("/");
  // The agent guide is the Guide page's other view, so the sidebar marks Guide for it too.
  const destination = guideRoutes.includes(page) ? "guide" : page;
  const dataId = session.data?.dataId;
  // Each page the person opens counts once, by its name alone, and each Guide view on its own;
  // moving between a page's records or sections doesn't.
  useEffect(() => {
    if (dataId) countPageView(page);
  }, [page, dataId]);
  const link = (route: string, icon: IconName, label: string) => (
    <a
      href={"#" + route}
      aria-current={
        destination !== route
          ? undefined
          : route === "roadmaps" && id
            ? "true"
            : "page"
      }
    >
      <Icon name={icon} size={18} />
      {label}
    </a>
  );
  useEffect(() => {
    document.documentElement.dataset.theme =
      localStore.read("theme").value ?? "system";
  }, []);
  return (
    <div
      className="app"
      style={
        sidebar.width === null
          ? undefined
          : ({ "--sidebar-width": sidebar.width + "px" } as CSSProperties)
      }
    >
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
      <aside className="sidebar" ref={sidebarRef}>
        <a className="brand" href="#today">
          <span className="brand-mark">w.</span>
          <span>
            worklog<small>A clear record of progress</small>
          </span>
        </a>
        <div className="sidebar-scroll">
          <nav aria-label="Main navigation">
            {sections.map((section) => (
              // Each section is a group its label names, so a screen reader announces it on entry.
              <div
                key={section.label}
                className="nav-section"
                role="group"
                aria-labelledby={`nav-section-${section.label.toLowerCase()}`}
              >
                <p
                  className="nav-section-label"
                  id={`nav-section-${section.label.toLowerCase()}`}
                >
                  {section.label}
                </p>
                {section.destinations.map(({ route, icon, label }) => (
                  <Fragment key={route}>
                    {route === "roadmaps" ? (
                      <div className="nav-row">
                        {link(route, icon, label)}
                        <button
                          type="button"
                          className="nav-toggle"
                          aria-expanded={roadmapsOpen}
                          aria-controls="roadmap-subnav"
                          aria-label={
                            roadmapsOpen
                              ? "Collapse roadmaps"
                              : "Expand roadmaps"
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
                      link(route, icon, label)
                    )}
                    {route === "roadmaps" && session.data && (
                      <RoadmapNav
                        key={session.data.dataId}
                        open={roadmapsOpen}
                        selected={page === "roadmaps" ? id : undefined}
                      />
                    )}
                  </Fragment>
                ))}
              </div>
            ))}
          </nav>
          <div className="sidebar-note">
            On this machine. Private by default.
          </div>
        </div>
        <ResizeHandle
          label="Resize sidebar"
          panel={sidebarRef}
          min={SIDEBAR_MIN}
          max={() => SIDEBAR_MAX}
          onResize={sidebar.resize}
          onReset={sidebar.reset}
        />
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
            <Search query={search} onChoose={chooseResult} />
          ) : page === "today" ? (
            <Today
              key={id ?? "today"}
              today={session.data!.today}
              initialDate={id}
            />
          ) : page === "work" ? (
            <Work key={id ?? "list"} selected={id} />
          ) : page === "tickets" ? (
            <Tickets key={id ?? "list"} selected={id} />
          ) : page === "roadmaps" ? (
            <Roadmaps key={id ?? "list"} selected={id} step={sub} />
          ) : page === "releases" ? (
            <Releases key={id ?? "list"} selected={id} />
          ) : page === "repositories" ? (
            <Repositories key={id ?? "list"} selected={id} />
          ) : page === "people" ? (
            <People key={id ?? "list"} selected={id} />
          ) : page === "resources" ? (
            <Resources selected={id} section={sub} />
          ) : page === "agents" ? (
            <Agents key={id ?? "list"} selected={id} />
          ) : page === "chats" ? (
            <Chats selected={id} />
          ) : page === "discussions" ? (
            <Discussions selected={id} />
          ) : page === "review" ? (
            <Review key={id ?? "list"} selectedId={id} />
          ) : page === "settings" ? (
            <Settings />
          ) : page === "usage" ? (
            <Usage />
          ) : guideRoutes.includes(page) ? (
            <Guide route={page} selected={id} />
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
function Search({
  query,
  onChoose,
}: {
  query: string;
  onChoose: () => void;
}) {
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
  const results = useData<Answer<"/api/v1/search">>(
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
            onClick={onChoose}
            href={
              r.routeType && r.routeId
                ? "#" + r.routeType + "/" + r.routeId
                : r.type === "roadmap"
                  ? "#roadmaps/" + r.id
                  : r.type === "person"
                    ? "#people/" + r.id
                    : "#work/" + (r.ownerId ?? r.id)
            }
          >
            {r.title || "Untitled"}
          </a>
          <p>{r.snippet}</p>
        </article>
      ))}
      <Pager
        offset={offset}
        total={results.data?.total ?? 0}
        onChange={setOffset}
        label="results"
      />
    </>
  );
}
