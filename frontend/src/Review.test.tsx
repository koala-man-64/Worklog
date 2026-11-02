import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { OperationDiff, Review } from "./Review";
import { ApiError, request } from "./api";
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  request: vi.fn(),
}));
const requestMock = vi.mocked(request);
const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const CREATED_NOTE = /^An earlier change in this proposal creates this target/;
function renderWithClient(ui: ReactNode) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {ui}
    </QueryClientProvider>,
  );
}
function show(action: string, payload: Record<string, unknown>) {
  renderWithClient(<OperationDiff action={action} payload={payload} />);
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
  ["repository.update", { id: ID }, `/repositories/${ID}`],
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
it("treats a missing step as new only when an earlier operation creates it", async () => {
  const roadmap = "6f1c0a52-3c4e-4b7a-9d1e-2a5b8c7d9e01";
  const existing = "1b2c3d4e-5f60-4718-8a9b-0c1d2e3f4a5b";
  const created = "2c3d4e5f-6071-4829-9bac-1d2e3f4a5b6c";
  const tooEarly = "3d4e5f60-7182-493a-8cbd-2e3f4a5b6c7d";
  const unknown = "4e5f6071-8293-4a4b-9dce-3f4a5b6c7d8e";
  const progress = (id: string) => ({
    action: "progress.set",
    payload: { id, revision: 1, status: "doing" },
  });
  const proposal = {
    id: ID,
    title: "Expand the roadmap",
    rationale: "",
    status: "submitted",
    revision: 2,
    actorLabel: "agent",
    attachments: [],
    operations: [
      // Operations apply in order, so this step does not exist yet here.
      progress(tooEarly),
      {
        action: "roadmap.revise",
        payload: {
          id: roadmap,
          revision: 4,
          title: "Roadmap",
          steps: [
            { id: existing, title: "Kept" },
            { id: created, title: "Added" },
            { id: tooEarly, title: "Added late" },
          ],
        },
      },
      progress(created),
      progress(existing),
      progress(unknown),
    ],
  };
  requestMock.mockImplementation(async (path: string) => {
    if (path.startsWith("/proposals?")) return { items: [], total: 0 };
    if (path === `/proposals/${ID}`) return proposal;
    if (path === `/roadmaps/${roadmap}`) return { id: roadmap, revision: 4 };
    if (path === `/steps/${existing}`)
      return { id: existing, revision: 1, status: "open" };
    throw new ApiError(404, "Step not found", "not_found");
  });
  renderWithClient(<Review selectedId={ID} />);
  expect(await screen.findByText(CREATED_NOTE)).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getAllByText("Step not found")).toHaveLength(2),
  );
  expect(screen.getAllByText(CREATED_NOTE)).toHaveLength(1);
  expect(screen.getByText("open")).toBeInTheDocument();
});
it("still reports a failed lookup that is not a missing target", async () => {
  requestMock.mockRejectedValue(new ApiError(500, "Server error", "internal"));
  renderWithClient(
    <OperationDiff
      action="progress.set"
      payload={{ id: ID, revision: 1, status: "doing" }}
      proposedEarlier={() => true}
    />,
  );
  expect(await screen.findByText("Server error")).toBeInTheDocument();
  expect(screen.queryByText(CREATED_NOTE)).toBeNull();
});
