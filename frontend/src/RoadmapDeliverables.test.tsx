import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import {
  RoadmapDeliverables,
  RoadmapDeliverablesSummary,
  StepDeliverables,
} from "./RoadmapDeliverables";
import type {
  DeliverableDetail,
  DeliverableHistoryAnswer,
  DeliverableSummary,
} from "./deliverableTypes";

const ROOT = "10000000-0000-4000-8000-000000000001";
const CHILD = "10000000-0000-4000-8000-000000000002";
const STEP = "20000000-0000-4000-8000-000000000001";
const DELIVERABLE = "30000000-0000-4000-8000-000000000001";
const PARENT_DELIVERABLE = "30000000-0000-4000-8000-000000000002";
const ATTACHMENT = "40000000-0000-4000-8000-000000000001";

function reference(label: string, href: string) {
  return {
    kind: "attachment" as const,
    id: ATTACHMENT,
    url: null,
    label,
    href,
    availability: "available" as const,
    sourceRevision: 3,
    sha256: "abc123-proof-hash",
  };
}

function detail(overrides: Partial<DeliverableDetail> = {}): DeliverableDetail {
  return {
    id: DELIVERABLE,
    roadmapId: CHILD,
    roadmapTitle: "Implementation",
    roadmapPath: [
      { id: ROOT, title: "Research program" },
      { id: CHILD, title: "Implementation" },
    ],
    title: "Buildable research repo",
    description: "A clean checkout builds and tests pass.",
    status: "planned",
    archived: false,
    revision: 2,
    requirementCount: 1,
    checkedCount: 0,
    createdAt: "2026-11-27T12:00:00Z",
    updatedAt: "2026-11-27T12:00:00Z",
    stepIds: [STEP],
    steps: [
      {
        id: STEP,
        title: "Implement build workflow",
        roadmapId: CHILD,
        roadmapTitle: "Implementation",
        available: true,
      },
    ],
    requirements: [
      {
        id: "50000000-0000-4000-8000-000000000001",
        text: "A fresh clone builds successfully",
        checked: false,
        verificationNote: "",
        evidence: [reference("Build log", `/api/v1/attachments/${ATTACHMENT}?download=1`)],
      },
    ],
    inputs: [
      {
        kind: "url",
        id: null,
        url: "https://example.test/design",
        label: "Design brief",
        href: "https://example.test/design",
        availability: "available",
        sourceRevision: null,
        sha256: null,
      },
    ],
    outputs: [reference("Build log", `/api/v1/attachments/${ATTACHMENT}?download=1`)],
    ...overrides,
  };
}

function summary(
  id: string,
  roadmapId: string,
  roadmapTitle: string,
  roadmapPath: { id: string; title: string }[],
): DeliverableSummary {
  return {
    id,
    roadmapId,
    roadmapTitle,
    roadmapPath,
    title: `${roadmapTitle} deliverable`,
    status: "planned",
    archived: false,
    revision: 1,
    requirementCount: 1,
    checkedCount: 0,
    createdAt: "2026-11-27T12:00:00Z",
    updatedAt: "2026-11-27T12:00:00Z",
  };
}

function acceptedHistory(): DeliverableHistoryAnswer {
  const accepted = detail({
    status: "accepted",
    revision: 4,
    checkedCount: 1,
    requirements: [
      {
        id: "50000000-0000-4000-8000-000000000001",
        text: "A fresh clone builds successfully",
        checked: true,
        verificationNote: "Verified from a clean checkout.",
        evidence: [reference("Build log", `/api/v1/attachments/${ATTACHMENT}?download=1`)],
      },
    ],
  });
  return {
    items: [
      {
        revision: 3,
        acceptedAt: "2026-11-27T15:30:00Z",
        actor: { id: "human", label: "Rudy", kind: "human" },
        note: "Reviewed and accepted.",
        proposalId: null,
        snapshot: {
          title: accepted.title,
          description: accepted.description,
          requirements: accepted.requirements,
          inputs: accepted.inputs,
          outputs: accepted.outputs,
          steps: accepted.steps,
        },
      },
    ],
    total: 1,
    events: [
      {
        kind: "deliverable.accept",
        actorId: "human",
        actorLabel: "Rudy",
        actorKind: "human",
        createdAt: "2026-11-27T15:30:00Z",
        reason: "Reviewed and accepted.",
      },
    ],
  };
}

function respond(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderWithClient(ui: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {ui}
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext(`deliverables-test-${crypto.randomUUID()}`);
  window.location.hash = "";
});

it("groups descendant deliverables by owner and opens the canonical owner route", async () => {
  const child = summary(CHILD, CHILD, "Implementation", [
    { id: ROOT, title: "Research program" },
    { id: CHILD, title: "Implementation" },
  ]);
  const root = summary(DELIVERABLE, ROOT, "Research program", [
    { id: ROOT, title: "Research program" },
  ]);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), window.location.origin);
    if (url.pathname === `/api/v1/roadmaps/${ROOT}/deliverables`)
      return respond({
        items: [root, child],
        total: 2,
        counts: { planned: 2, readyForReview: 0, accepted: 0 },
      });
    return respond({ items: [], total: 0, counts: { planned: 0, readyForReview: 0, accepted: 0 } });
  });

  renderWithClient(<RoadmapDeliverables roadmapId={ROOT} />);
  expect(await screen.findByRole("heading", { name: "Research program" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Research program › Implementation" })).toBeVisible();
  await userEvent.click(screen.getByRole("link", { name: "Implementation deliverable" }));
  expect(window.location.hash).toBe(`#roadmaps/${CHILD}/deliverables/${child.id}`);
});

it("shows tree counts on Status and links a child step back to its owning roadmap deliverable", async () => {
  const parent = summary(PARENT_DELIVERABLE, ROOT, "Research program", [
    { id: ROOT, title: "Research program" },
  ]);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), window.location.origin);
    if (url.pathname === `/api/v1/roadmaps/${CHILD}/deliverables`)
      return respond({
        items: [parent],
        total: 1,
        counts: { planned: 1, readyForReview: 0, accepted: 0 },
      });
    if (url.pathname === `/api/v1/steps/${STEP}/deliverables`)
      return respond({ items: [parent], total: 1 });
    return respond({ items: [], total: 0, counts: { planned: 0, readyForReview: 0, accepted: 0 } });
  });

  renderWithClient(
    <>
      <RoadmapDeliverablesSummary roadmapId={CHILD} />
      <StepDeliverables roadmapId={CHILD} stepId={STEP} />
    </>,
  );
  expect(await screen.findByText("1 planned · 0 ready for review · 0 accepted")).toBeVisible();
  await userEvent.click(await screen.findByRole("link", { name: "Research program deliverable" }));
  expect(window.location.hash).toBe(`#roadmaps/${ROOT}/deliverables/${PARENT_DELIVERABLE}`);
});

it("requires checked, evidenced requirements and keeps submit, accept, and reopen independent of step progress", async () => {
  let current = detail();
  const calls: { path: string; method: string; body: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), window.location.origin);
    const path = url.pathname.replace("/api/v1", "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    if (method !== "GET") calls.push({ path, method, body });
    if (path === `/deliverables/${DELIVERABLE}` && method === "GET")
      return respond(current);
    if (path === `/deliverables/${DELIVERABLE}/history`)
      return respond({ items: [], total: 0, events: [] });
    if (path === `/deliverables/${DELIVERABLE}` && method === "PATCH") {
      if (Array.isArray(body.requirements)) {
        current = {
          ...current,
          requirements: current.requirements.map((requirement) => {
            const updated = (body.requirements as { id?: string; checked: boolean }[]).find(
              (row) => row.id === requirement.id,
            );
            return updated ? { ...requirement, checked: updated.checked } : requirement;
          }),
          checkedCount: current.requirements.filter((requirement) =>
            (body.requirements as { id?: string; checked: boolean }[]).some(
              (row) => row.id === requirement.id && row.checked,
            ),
          ).length,
          revision: current.revision + 1,
        };
      }
      return respond(current);
    }
    if (path.startsWith(`/deliverables/${DELIVERABLE}/`) && method === "POST") {
      const verb = path.split("/").at(-1);
      current = {
        ...current,
        status:
          verb === "submit"
            ? "ready_for_review"
            : verb === "accept"
              ? "accepted"
              : verb === "reopen"
                ? "planned"
                : current.status,
        requirements:
          verb === "reopen" && current.status === "accepted"
            ? current.requirements.map((requirement) => ({
                ...requirement,
                checked: false,
              }))
            : current.requirements,
        revision: current.revision + 1,
      };
      return respond(current);
    }
    return respond({ items: [], total: 0, events: [] });
  });

  renderWithClient(
    <RoadmapDeliverables
      roadmapId={CHILD}
      selectedId={DELIVERABLE}
    />,
  );
  const checkbox = await screen.findByRole("checkbox", {
    name: "Requirement 1 complete",
  });
  const submit = screen.getByRole("button", { name: "Submit for review" });
  expect(submit).toBeDisabled();
  await userEvent.click(checkbox);
  await waitFor(() => expect(checkbox).toBeChecked());
  await waitFor(() => expect(submit).toBeEnabled());
  await userEvent.click(submit);
  expect(await screen.findByRole("button", { name: "Accept deliverable" })).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Accept deliverable" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Acceptance note" }), "Reviewed output");
  await userEvent.click(screen.getByRole("button", { name: "Confirm acceptance" }));
  expect(await screen.findByRole("button", { name: "Reopen deliverable" })).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Reopen deliverable" }));
  const reason = screen.getByRole("textbox", { name: "Reason for reopening" });
  expect(screen.getByRole("button", { name: "Confirm reopening" })).toBeDisabled();
  await userEvent.type(reason, "Build instructions need correction.");
  await userEvent.click(screen.getByRole("button", { name: "Confirm reopening" }));
  await waitFor(() => expect(current.status).toBe("planned"));
  expect(calls.map((call) => call.path)).toEqual([
    `/deliverables/${DELIVERABLE}`,
    `/deliverables/${DELIVERABLE}/submit`,
    `/deliverables/${DELIVERABLE}/accept`,
    `/deliverables/${DELIVERABLE}/reopen`,
  ]);
  expect(calls[1].body.revision).toBe(3);
  expect(calls[2].body).toEqual({ revision: 4, note: "Reviewed output" });
  expect(calls[3].body).toEqual({
    revision: 5,
    reason: "Build instructions need correction.",
  });
});

it("shows immutable acceptance snapshot evidence, linked steps, and source hashes", async () => {
  const accepted = detail({ status: "accepted", checkedCount: 1 });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), window.location.origin);
    if (url.pathname === `/api/v1/deliverables/${DELIVERABLE}`)
      return respond(accepted);
    if (url.pathname === `/api/v1/deliverables/${DELIVERABLE}/history`)
      return respond(acceptedHistory());
    return respond({ items: [], total: 0, events: [] });
  });
  renderWithClient(
    <RoadmapDeliverables roadmapId={CHILD} selectedId={DELIVERABLE} />,
  );
  const snapshot = await screen.findByText(/Acceptance snapshot · revision 3/);
  await userEvent.click(snapshot);
  const snapshotDetails = snapshot.closest("details") as HTMLElement;
  expect(await within(snapshotDetails).findByText(/A fresh clone builds successfully/)).toBeVisible();
  expect(within(snapshotDetails).getAllByText("Build log")).toHaveLength(2);
  expect(within(snapshotDetails).getAllByText(/SHA-256 abc123-proof-hash/)).toHaveLength(2);
  expect(within(snapshotDetails).getByRole("link", { name: "Implement build workflow" })).toHaveAttribute(
    "href",
    `#roadmaps/${CHILD}/${STEP}`,
  );
  expect(within(snapshotDetails).getByRole("link", { name: "Design brief" })).toHaveAttribute(
    "href",
    "https://example.test/design",
  );
});
