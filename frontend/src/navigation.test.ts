import { expect, it } from "vitest";
import { destinations, sections } from "./navigation";

it("groups every page once, in K7's sections and sidebar order", () => {
  expect(sections.map(({ label }) => label)).toEqual([
    "Plan",
    "Directory",
    "Agents",
    "System",
  ]);
  expect(destinations.map(({ route }) => route)).toEqual([
    "today",
    "work",
    "tickets",
    "roadmaps",
    "repositories",
    "people",
    "resources",
    "agents",
    "chats",
    "discussions",
    "review",
    "settings",
    "usage",
    "guide",
  ]);
  expect(new Set(destinations.map(({ route }) => route)).size).toBe(
    destinations.length,
  );
});
