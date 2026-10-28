import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Today } from "./Today";
vi.mock("./hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./hooks")>()),
  useData: (path: string) => ({
    data: path.startsWith("/days/")
      ? {
          date: "2026-10-20",
          revision: 7,
          reflection: "",
          entries: [
            {
              id: "historical-entry",
              itemId: "historical-item",
              planned: true,
              worked: true,
              outcome: "Recorded before archiving",
              revision: 1,
              item: {
                id: "historical-item",
                title: "Archived work",
                status: "done",
                archived: true,
                revision: 4,
              },
            },
          ],
          summary: { worked: [{}], completed: [], blocked: [] },
          nextItemId: null,
        }
      : { items: [], total: 0 },
  }),
}));
it("uses historical API summary and makes archived actuals read only", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Today today="2026-10-28" initialDate="2026-10-20" />
    </QueryClientProvider>,
  );
  expect(
    [...document.querySelectorAll(".day-summary strong")].map(
      (el) => el.textContent,
    ),
  ).toEqual(["1", "0", "0"]);
  expect(screen.getByRole("combobox", { name: "Status" })).toBeDisabled();
  expect(screen.getByLabelText("Worked on", { exact: true })).toBeDisabled();
  expect(screen.getByText("Recorded before archiving")).toBeInTheDocument();
  expect(
    screen.queryByRole("textbox", { name: "Outcome / next action" }),
  ).toBeNull();
  expect(screen.getByRole("button", { name: "Reschedule" })).toBeDisabled();
});
