import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { RoadmapDetail } from "./Roadmaps";
import type { Roadmap, RoadmapDecision, RoadmapQuestion } from "./types";

const ROADMAP = "33333333-3333-4333-8333-333333333333";
const OPEN = "dddddddd-0000-4000-8000-000000000001";
const ANSWERED = "dddddddd-0000-4000-8000-000000000002";
const DECISION = "eeeeeeee-0000-4000-8000-000000000001";
const question = (
  id: string,
  title: string,
  fields: Partial<RoadmapQuestion> = {},
): RoadmapQuestion => ({
  id,
  roadmapId: ROADMAP,
  title,
  details: "",
  answer: "",
  status: "open",
  revision: 1,
  archived: false,
  ...fields,
});
const decision = (
  id: string,
  title: string,
  fields: Partial<RoadmapDecision> = {},
): RoadmapDecision => ({
  id,
  roadmapId: ROADMAP,
  title,
  rationale: "",
  decidedDate: "2026-09-01",
  revision: 1,
  archived: false,
  ...fields,
});
const road: Roadmap = {
  id: ROADMAP,
  revision: 1,
  title: "Storage",
  overview: "",
  steps: [],
  versions: [],
  notes: [],
  attachments: [],
  history: [],
  questions: [
    question(OPEN, "Which region hosts replicas?", {
      details: "Latency budget is **40 ms**.",
      revision: 2,
    }),
    question(ANSWERED, "Do we need sharding?", {
      status: "answered",
      answer: "Not below 10 GB.",
      revision: 3,
    }),
  ],
  decisions: [
    decision(DECISION, "Use SQLite", {
      rationale: "One file, no server.",
      revision: 4,
    }),
  ],
};
let served = road;
let writes: { url: string; method: string; body: unknown }[] = [];
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("questions-decisions-" + crypto.randomUUID());
  served = road;
  writes = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const method = init?.method ?? "GET";
    if (method !== "GET")
      writes.push({
        url: String(input).replace(/^.*\/api\/v1/, ""),
        method,
        body: JSON.parse(String(init?.body)),
      });
    return Response.json(
      String(input).endsWith("/roadmaps/" + ROADMAP)
        ? served
        : { items: [], total: 0 },
    );
  });
});
const view = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RoadmapDetail id={ROADMAP} />
    </QueryClientProvider>,
  );
const section = async (name: string) =>
  (await screen.findByRole("heading", { name: new RegExp("^" + name) }))
    .parentElement as HTMLElement;

it("lists open questions, keeps answered ones with their answers, and asks a new one", async () => {
  view();
  const questions = await section("Questions");
  expect(within(questions).getByText("1 open")).toBeVisible();
  expect(within(questions).getByText("40 ms")).toBeVisible();
  const answered = within(questions).getByText("Answered (1)");
  expect(within(questions).getByText("Not below 10 GB.")).not.toBeVisible();
  await userEvent.click(answered);
  expect(within(questions).getByText("Not below 10 GB.")).toBeVisible();

  await userEvent.type(
    within(questions).getByRole("textbox", { name: "New question" }),
    "  Who owns backups?  ",
  );
  await userEvent.click(
    within(questions).getByRole("button", { name: "Add question" }),
  );
  expect(writes).toEqual([
    {
      url: "/questions",
      method: "POST",
      body: { roadmapId: ROADMAP, title: "Who owns backups?", details: "" },
    },
  ]);
});

it("answers a question only with an answer, and reopens one without losing it", async () => {
  view();
  const questions = await section("Questions");
  await userEvent.click(
    within(questions).getByRole("button", {
      name: "Answer question Which region hosts replicas?",
    }),
  );
  const save = within(questions).getByRole("button", { name: "Save answer" });
  expect(save).toBeDisabled();
  // The details stay in view while answering.
  expect(within(save.closest("form")!).getByText("40 ms")).toBeVisible();
  await userEvent.type(
    within(questions).getByRole("textbox", { name: "Answer" }),
    "Frankfurt",
  );
  await userEvent.click(save);
  await userEvent.click(within(questions).getByText("Answered (1)"));
  await userEvent.click(
    within(questions).getByRole("button", {
      name: "Reopen question Do we need sharding?",
    }),
  );
  expect(writes).toEqual([
    {
      url: "/questions/" + OPEN,
      method: "PATCH",
      body: { revision: 2, answer: "Frankfurt", status: "answered" },
    },
    {
      url: "/questions/" + ANSWERED,
      method: "PATCH",
      body: { revision: 3, status: "open" },
    },
  ]);
});

it("edits a question's text, and an answered question's answer with it", async () => {
  view();
  const questions = await section("Questions");
  await userEvent.click(
    within(questions).getByRole("button", {
      name: "Edit question Which region hosts replicas?",
    }),
  );
  expect(
    within(questions).queryByRole("textbox", { name: "Answer" }),
  ).toBeNull();
  const title = within(questions).getByRole("textbox", { name: "Question" });
  await userEvent.clear(title);
  await userEvent.type(title, "Which region hosts read replicas?");
  await userEvent.click(
    within(questions).getByRole("button", { name: "Save question" }),
  );

  await userEvent.click(within(questions).getByText("Answered (1)"));
  await userEvent.click(
    within(questions).getByRole("button", {
      name: "Edit question Do we need sharding?",
    }),
  );
  const answer = within(questions).getByRole("textbox", { name: "Answer" });
  await userEvent.clear(answer);
  expect(
    within(questions).getByRole("button", { name: "Save question" }),
  ).toBeDisabled();
  await userEvent.type(answer, "Not below 50 GB.");
  await userEvent.click(
    within(questions).getByRole("button", { name: "Save question" }),
  );
  expect(writes).toEqual([
    {
      url: "/questions/" + OPEN,
      method: "PATCH",
      body: {
        revision: 2,
        title: "Which region hosts read replicas?",
        details: "Latency budget is **40 ms**.",
      },
    },
    {
      url: "/questions/" + ANSWERED,
      method: "PATCH",
      body: {
        revision: 3,
        title: "Do we need sharding?",
        details: "",
        answer: "Not below 50 GB.",
      },
    },
  ]);
});

it("records decisions with an optional date and rationale, and edits them", async () => {
  view();
  const decisions = await section("Decisions");
  expect(within(decisions).getByText("Decided 2026-09-01")).toBeVisible();
  expect(within(decisions).getByText("One file, no server.")).toBeVisible();

  await userEvent.type(
    within(decisions).getByRole("textbox", { name: "New decision" }),
    "Keep the rollback journal",
  );
  await userEvent.click(
    within(decisions).getByRole("button", { name: "Record decision" }),
  );
  await userEvent.type(
    within(decisions).getByRole("textbox", { name: "New decision" }),
    "Back up daily",
  );
  await userEvent.type(
    within(decisions).getByRole("textbox", { name: "New decision rationale" }),
    "Restores need a recent snapshot.",
  );
  const dates = within(decisions).getAllByLabelText("Date decided");
  await userEvent.type(dates[dates.length - 1], "2026-10-30");
  await userEvent.click(
    within(decisions).getByRole("button", { name: "Record decision" }),
  );

  await userEvent.click(
    within(decisions).getByRole("button", { name: "Edit decision Use SQLite" }),
  );
  const rationale = within(decisions).getByRole("textbox", {
    name: "Decision rationale",
  });
  await userEvent.type(rationale, " Easy backups.");
  await userEvent.click(
    within(decisions).getByRole("button", { name: "Save decision" }),
  );
  expect(writes).toEqual([
    {
      url: "/decisions",
      method: "POST",
      body: {
        roadmapId: ROADMAP,
        title: "Keep the rollback journal",
        rationale: "",
      },
    },
    {
      url: "/decisions",
      method: "POST",
      body: {
        roadmapId: ROADMAP,
        title: "Back up daily",
        rationale: "Restores need a recent snapshot.",
        decidedDate: "2026-10-30",
      },
    },
    {
      url: "/decisions/" + DECISION,
      method: "PATCH",
      body: {
        revision: 4,
        title: "Use SQLite",
        rationale: "One file, no server. Easy backups.",
        decidedDate: "2026-09-01",
      },
    },
  ]);
});

it("shows an archived roadmap's questions and decisions read-only", async () => {
  served = { ...road, archived: true };
  view();
  const questions = await section("Questions");
  const decisions = await section("Decisions");
  expect(within(questions).queryByRole("button")).toBeNull();
  expect(within(questions).queryByRole("textbox")).toBeNull();
  expect(within(decisions).queryByRole("button")).toBeNull();
  expect(within(decisions).queryByRole("textbox")).toBeNull();
  expect(within(decisions).getByText("Use SQLite")).toBeVisible();
});

it("recovers archived questions and decisions", async () => {
  served = {
    ...road,
    questions: [question(OPEN, "Parked question", { archived: true })],
    decisions: [decision(DECISION, "Parked decision", { archived: true })],
  };
  view();
  expect(await screen.findByText("No open questions.")).toBeVisible();
  expect(screen.getByText("No decisions recorded yet.")).toBeVisible();
  await userEvent.click(screen.getByText(/Archived supporting records \(/));
  const parked = screen.getByText("Parked decision").closest(".line")!;
  await userEvent.click(within(parked as HTMLElement).getByRole("button"));
  expect(writes).toEqual([
    {
      url: `/decisions/${DECISION}/recover`,
      method: "POST",
      body: { revision: 1 },
    },
  ]);
  expect(screen.getByText("Parked question")).toBeVisible();
});
