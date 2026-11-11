import type { IconName } from "./icons";

type Destination = { route: string; icon: IconName; label: string };
type Section = { label: string; destinations: Destination[] };

/**
 * The sidebar's sections and their pages, in order (K7). Releases, added after K7, plan work as
 * Tickets and Roadmaps do, so they sit in Plan. The guide describes each page under its Pages
 * heading.
 */
export const sections: Section[] = [
  {
    label: "Plan",
    destinations: [
      { route: "today", icon: "today", label: "Today" },
      { route: "work", icon: "work", label: "Work" },
      { route: "tickets", icon: "tickets", label: "Tickets" },
      { route: "roadmaps", icon: "roadmaps", label: "Roadmaps" },
      { route: "releases", icon: "releases", label: "Releases" },
    ],
  },
  {
    label: "Directory",
    destinations: [
      { route: "repositories", icon: "work", label: "Repositories" },
      { route: "people", icon: "people", label: "People" },
      { route: "resources", icon: "resources", label: "Resources" },
    ],
  },
  {
    label: "Agents",
    destinations: [
      { route: "agents", icon: "agents", label: "Agents" },
      { route: "chats", icon: "chats", label: "Chats" },
      { route: "discussions", icon: "chats", label: "Discussions" },
      { route: "review", icon: "review", label: "Review" },
    ],
  },
  {
    label: "System",
    destinations: [
      { route: "settings", icon: "settings", label: "Settings" },
      { route: "usage", icon: "usage", label: "Usage" },
      { route: "guide", icon: "guide", label: "Guide" },
    ],
  },
];

/** Every page, in sidebar order. */
export const destinations: Destination[] = sections.flatMap(
  (section) => section.destinations,
);
