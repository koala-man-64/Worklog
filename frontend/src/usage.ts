import type { Schema } from "./apiTypes";
import { commands } from "./commands";
import { guideRoutes } from "./Guide";
import { destinations } from "./navigation";

/** The pages counted: the sidebar's, and each view of the Guide page. */
export const countedPages = [
  ...new Set([...destinations.map(({ route }) => route), ...guideRoutes]),
];
const pages = new Set<string>(countedPages);
/** Whether the backend counts the page; test_usage.py keeps its list and the sidebar's equal. */
const counted = (page: string): page is Schema["PageView"]["page"] =>
  pages.has(page);

/**
 * Counts one visit to a page, by its name alone, so `worklog usage` can tell which pages are used
 * and which aren't. Best effort: a count that fails is dropped and never shown.
 */
export function countPageView(page: string) {
  if (!counted(page)) return;
  commands.countPageView({ page }).catch(() => undefined);
}
