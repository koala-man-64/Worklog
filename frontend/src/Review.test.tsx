import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { OperationDiff } from "./Review";
import { request } from "./api";
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  request: vi.fn(),
}));
const requestMock = vi.mocked(request);
const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
function show(action: string, payload: Record<string, unknown>) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <OperationDiff action={action} payload={payload} />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  requestMock.mockReset();
  requestMock.mockResolvedValue({ entries: [], revision: 1 });
});
it("sends no request and warns when the date is not a calendar date", async () => {
  show("entry.upsert", { itemId: ID, date: "../export" });
  expect(await screen.findByText(/^Malformed target/)).toBeInTheDocument();
  expect(requestMock).not.toHaveBeenCalled();
});
it.each([
  ["note.update", { id: "../export", revision: 1 }],
  ["task.update", { id: "../../export?x=1" }],
  ["work-item.update", { id: `${ID}/../export` }],
  ["progress.set", { stepId: "..%2Fexport" }],
  ["roadmap.update", { roadmapId: "export" }],
  ["entry.upsert", { itemId: "../export", date: "2026-10-28" }],
])("sends no request for a malformed %s target", async (action, payload) => {
  show(action, payload);
  expect(await screen.findByText(/^Malformed target/)).toBeInTheDocument();
  expect(requestMock).not.toHaveBeenCalled();
});
it.each([
  ["entry.upsert", { itemId: ID, date: "2026-10-28" }, "/days/2026-10-28"],
  ["note.update", { id: ID }, `/notes/${ID}`],
  ["task.update", { id: ID }, `/tasks/${ID}`],
  ["work-item.update", { id: ID }, `/work-items/${ID}`],
  ["roadmap.update", { roadmapId: ID }, `/roadmaps/${ID}`],
  ["progress.set", { stepId: ID }, `/steps/${ID}`],
])(
  "fetches the encoded path for a valid %s payload",
  async (action, payload, path) => {
    show(action, payload);
    await waitFor(() => expect(requestMock).toHaveBeenCalledWith(path));
    expect(screen.queryByText(/^Malformed target/)).toBeNull();
  },
);
it("does not look anything up for create operations", () => {
  show("note.create", { id: "../export" });
  expect(requestMock).not.toHaveBeenCalled();
  expect(screen.queryByText(/^Malformed target/)).toBeNull();
});
