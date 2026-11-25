import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "../api";
import { ChangeHistory } from "./History";

// Audit 3.1 (P1): the roadmap, work item and person reads carried their last 100 changes whole, and
// GET /history did too. The history now loads when opened, as a list of who changed what and when,
// and one change's before and after only when asked for.
const ID = "11111111-1111-4111-8111-111111111111";
const change = (id: string, kind: string, createdAt: string) => ({
  id,
  entityId: ID,
  kind,
  actorId: "local-user",
  actorLabel: "You",
  actorKind: "human",
  createdAt,
  localDate: createdAt.slice(0, 10),
});
let paths: string[] = [];
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("history-test-" + crypto.randomUUID());
  paths = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input).replace("/api/v1", "");
    paths.push(path);
    const body = path.includes("&eventId=")
      ? {
          items: [
            {
              ...change("e2", "roadmap.update", "2026-11-20T10:00:00Z"),
              before: { title: "Old plan" },
              after: { title: "New plan" },
            },
          ],
        }
      : {
          items: [
            change("e2", "roadmap.update", "2026-11-20T10:00:00Z"),
            change("e1", "roadmap.create", "2026-11-19T09:00:00Z"),
          ],
        };
    return new Response(JSON.stringify(body), { status: 200 });
  });
});
const view = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ChangeHistory id={ID} />
    </QueryClientProvider>,
  );

it("lists the changes only when the history is opened, without their records", async () => {
  view();
  expect(paths).toEqual([]);
  await userEvent.click(screen.getByText("Activity history"));
  const list = await screen.findByRole("list", { name: "Changes" });
  expect(
    within(list)
      .getAllByRole("listitem")
      .map((row) => row.querySelector(".change-kind")?.textContent),
  ).toEqual(["roadmap.update", "roadmap.create"]);
  expect(paths).toEqual(["/history?entityId=" + ID]);
  expect(screen.queryByText(/Old plan/)).toBeNull();
});

it("loads one change's before and after when it is opened", async () => {
  view();
  await userEvent.click(screen.getByText("Activity history"));
  const list = await screen.findByRole("list", { name: "Changes" });
  const [latest] = within(list).getAllByRole("listitem");
  await userEvent.click(within(latest).getByText("Show the change"));
  expect(await within(latest).findByText(/Old plan/)).toBeInTheDocument();
  expect(within(latest).getByText(/New plan/)).toBeInTheDocument();
  expect(paths).toEqual([
    "/history?entityId=" + ID,
    `/history?entityId=${ID}&eventId=e2`,
  ]);
});
