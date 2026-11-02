import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
const OTHER_ID = "5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d";
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
it("says when approval is off and marks automatic approvals", async () => {
  const proposal = (id: string, autoApproved: boolean) => ({
    id,
    title: autoApproved ? "Applied on submit" : "Approved in review",
    rationale: "",
    status: "approved",
    autoApproved,
    revision: 3,
    actorLabel: "Parser agent",
    operations: [],
    attachments: [],
  });
  requestMock.mockImplementation(async (path: string) => {
    if (path === "/settings") return { requireProposalApproval: false };
    if (path === "/proposals?status=submitted") return { items: [], total: 0 };
    if (path === "/proposals?status=approved")
      return {
        items: [proposal(ID, true), proposal(OTHER_ID, false)],
        total: 2,
      };
    throw new ApiError(404, "Not found", "not_found");
  });
  renderWithClient(<Review />);
  expect(await screen.findByText(/^Approval is off/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
    "href",
    "#settings",
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Proposal status" }),
    "approved",
  );
  // A proposal the person approved keeps the plain status.
  const badges = await screen.findAllByText(/^approved/, {
    selector: ".badge",
  });
  expect(badges.map((badge) => badge.textContent)).toEqual([
    "approved automatically",
    "approved",
  ]);
});
it("shows an automatic approval in a proposal's detail", async () => {
  requestMock.mockImplementation(async (path: string) => {
    if (path === `/proposals/${ID}`)
      return {
        id: ID,
        title: "Applied on submit",
        rationale: "",
        status: "approved",
        autoApproved: true,
        revision: 3,
        actorLabel: "Parser agent",
        operations: [],
        attachments: [],
      };
    if (path === "/settings") return { requireProposalApproval: true };
    return { items: [], total: 0 };
  });
  renderWithClient(<Review selectedId={ID} />);
  expect(
    await screen.findByText(
      "Proposed by Parser agent, approved automatically, revision 3",
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/^Approval is off/)).toBeNull();
});
const STARTED = "2026-11-02T14:00:00.000001+00:00";
const SUBMITTED = "2026-11-02T15:00:00.123456+00:00";
const CHANGED = "2026-11-02T16:30:00.5+00:00";
const local = (iso: string) => new Date(iso).toLocaleString();
function dated(
  title: string,
  status: string,
  submittedAt: string | null,
  updatedAt: string,
) {
  return {
    id: title,
    title,
    rationale: "",
    status,
    autoApproved: false,
    revision: 2,
    actorLabel: "Engineer",
    operations: [],
    attachments: [],
    createdAt: STARTED,
    submittedAt,
    updatedAt,
  };
}
it("dates each listed proposal by its submission and any later update", async () => {
  requestMock.mockImplementation(async (path: string) => {
    if (path === "/settings") return { requireProposalApproval: true };
    return {
      items: [
        dated("Waiting", "submitted", SUBMITTED, SUBMITTED),
        // Submitted before its record took the submit event's time, so its
        // update came a moment before the submission.
        dated(
          "Waiting longer",
          "submitted",
          SUBMITTED,
          "2026-11-02T15:00:00.1Z",
        ),
        dated("Decided", "rejected", SUBMITTED, CHANGED),
        dated("New draft", "draft", null, STARTED),
        dated("Edited draft", "draft", null, CHANGED),
      ],
      total: 5,
    };
  });
  renderWithClient(<Review />);
  const dates = async (title: string) =>
    (await screen.findByRole("link", { name: title }))
      .closest("article")
      ?.querySelectorAll("p")[1]?.textContent;
  expect(await dates("Waiting")).toBe(`Submitted ${local(SUBMITTED)}`);
  expect(await dates("Waiting longer")).toBe(`Submitted ${local(SUBMITTED)}`);
  expect(await dates("Decided")).toBe(
    `Submitted ${local(SUBMITTED)} · updated ${local(CHANGED)}`,
  );
  expect(await dates("New draft")).toBe(`Drafted ${local(STARTED)}`);
  expect(await dates("Edited draft")).toBe(
    `Drafted ${local(STARTED)} · updated ${local(CHANGED)}`,
  );
});
it("dates a proposal's detail", async () => {
  requestMock.mockImplementation(async (path: string) => {
    if (path === `/proposals/${ID}`)
      return { ...dated("Decided", "approved", SUBMITTED, CHANGED), id: ID };
    if (path === "/settings") return { requireProposalApproval: true };
    return { items: [], total: 0 };
  });
  renderWithClient(<Review selectedId={ID} />);
  const time = await screen.findByText(local(CHANGED));
  expect(time).toHaveAttribute("datetime", CHANGED);
  expect(time.closest("p")).toHaveTextContent(
    `Submitted ${local(SUBMITTED)} · updated ${local(CHANGED)}`,
  );
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
it("says what approving a repository registration would do", async () => {
  const registered = {
    id: ID,
    name: "Service",
    url: "https://github.com/a/service",
    remoteIdentity: "github.com/a/service",
    duplicateOf: null,
    mergedInto: null,
    description: "",
    revision: 1,
    archived: false,
  };
  requestMock.mockImplementation(async (path: string) =>
    path.includes("service")
      ? {
          url: path,
          remoteIdentity: "github.com/a/service",
          repository: registered,
        }
      : path.includes("retired")
        ? {
            url: path,
            remoteIdentity: "",
            repository: {
              ...registered,
              name: "Retired",
              url: "https://github.com/a/retired",
              archived: true,
            },
          }
        : { url: path, remoteIdentity: "github.com/a/new", repository: null },
  );
  renderWithClient(
    <>
      <OperationDiff
        number={1}
        action="repository.create"
        payload={{ name: "Service", url: "git@github.com:a/service.git" }}
      />
      <OperationDiff
        number={2}
        action="repository.create"
        payload={{ name: "Retired", url: "https://github.com/a/retired" }}
      />
      <OperationDiff
        number={3}
        action="repository.create"
        payload={{ name: "New", url: "https://github.com/a/new" }}
      />
    </>,
  );
  expect(await screen.findByText(/^Already registered as/)).toHaveTextContent(
    "Already registered as Service: approval links to it and adds nothing.",
  );
  expect(await screen.findByText(/^Registered as/)).toHaveTextContent(
    "Registered as Retired, which is archived: recover it, or approval fails.",
  );
  // The record a registration would link to is the current value it is compared with.
  expect(screen.getByText("https://github.com/a/service")).toBeVisible();
  expect(screen.getAllByText("New record")).toHaveLength(2);
  // Each change carries the number a failed approval names it by.
  expect(
    screen.getByRole("heading", { name: "Change 3: repository.create" }),
  ).toBeVisible();
  expect(requestMock).toHaveBeenCalledWith(
    "/repositories/resolve?url=" +
      encodeURIComponent("git@github.com:a/service.git"),
  );
  expect(screen.getAllByText(/registered as/i)).toHaveLength(2);
});
