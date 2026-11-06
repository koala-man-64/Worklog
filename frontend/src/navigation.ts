import type { IconName } from "./icons";

type Destination = { route: string; icon: IconName; label: string };

/** The sidebar's pages in order. The guide describes each one under its Pages heading. */
export const destinations: Destination[] = [
  { route: "today", icon: "today", label: "Today" },
  { route: "work", icon: "work", label: "Work" },
  { route: "tickets", icon: "tickets", label: "Tickets" },
  { route: "roadmaps", icon: "roadmaps", label: "Roadmaps" },
  { route: "releases", icon: "releases", label: "Releases" },
  { route: "repositories", icon: "work", label: "Repositories" },
  { route: "people", icon: "people", label: "People" },
  { route: "resources", icon: "resources", label: "Resources" },
  { route: "agents", icon: "agents", label: "Agents" },
  { route: "chats", icon: "chats", label: "Chats" },
  { route: "discussions", icon: "chats", label: "Discussions" },
  { route: "review", icon: "review", label: "Review" },
  { route: "settings", icon: "settings", label: "Settings" },
  { route: "guide", icon: "guide", label: "Guide" },
];
