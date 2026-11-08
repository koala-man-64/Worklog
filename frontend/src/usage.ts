import { request } from "./api";
import { guideRoutes } from "./Guide";
import { destinations } from "./navigation";

/** The pages counted: the sidebar's, and each view of the Guide page. */
export const countedPages = [
  ...new Set([...destinations.map(({ route }) => route), ...guideRoutes]),
];
const pages = new Set(countedPages);

/**
 * Counts one visit to a page, by its name alone, so `worklog usage` can tell which pages are used
 * and which aren't. Best effort: a count that fails is dropped and never shown.
 */
export function countPageView(page: string) {
  if (!pages.has(page)) return;
  request("/page-views", "POST", { page }).catch(() => undefined);
}
