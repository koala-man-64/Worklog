import { expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DraftField, Markdown } from "./components";
import { setDataContext } from "./api";
function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {children}
    </QueryClientProvider>
  );
}
it("renders GFM without executing imported HTML or unsafe links", () => {
  render(
    <Markdown
      text={
        "# Heading\n\n- [x] Finished\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))"
      }
    />,
  );
  expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
  expect(screen.getByText("bad")).not.toHaveAttribute(
    "href",
    "javascript:alert(1)",
  );
});
it("renders image references as alt text and isolates external links", () => {
  render(
    <Markdown
      text={
        "![Evidence diagram](https://tracker.example/image.png)\n\n[Reference](https://example.com)\n\n[Local](#work)"
      }
    />,
  );
  expect(document.querySelector("img")).toBeNull();
  expect(screen.getByText("[Image: Evidence diagram]")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Reference" })).toHaveAttribute(
    "target",
    "_blank",
  );
  expect(screen.getByRole("link", { name: "Reference" })).toHaveAttribute(
    "rel",
    "noopener noreferrer",
  );
  expect(screen.getByRole("link", { name: "Local" })).not.toHaveAttribute(
    "target",
  );
});
it("keeps the local draft after a revision conflict", async () => {
  const save = vi.fn().mockRejectedValue({ status: 409 });
  render(
    <DraftField
      draftKey="test-conflict"
      label="Outcome"
      initial=""
      revision={1}
      save={save}
    />,
    { wrapper },
  );
  await userEvent.type(screen.getByLabelText("Outcome"), "Unlost thought");
  await userEvent.click(screen.getByRole("button", { name: "Save now" }));
  await screen.findByText(/Conflict — draft kept/);
  expect(localStorage.getItem("worklog:test:draft:test-conflict")).toBe(
    "Unlost thought",
  );
  expect(save).toHaveBeenCalledTimes(1);
});
it("recovers and saves a locally persisted draft", async () => {
  localStorage.setItem("worklog:test:draft:test-recovered", "Recovered");
  localStorage.setItem("worklog:test:draft-base:test-recovered", "1");
  const save = vi.fn().mockResolvedValue({ revision: 2 });
  render(
    <DraftField
      draftKey="test-recovered"
      label="Note"
      initial="old"
      revision={1}
      save={save}
    />,
    { wrapper },
  );
  expect(screen.getByLabelText("Note")).toHaveValue("Recovered");
  await userEvent.click(screen.getByRole("button", { name: "Save now" }));
  await waitFor(() =>
    expect(
      localStorage.getItem("worklog:test:draft:test-recovered"),
    ).toBeNull(),
  );
  expect(save).toHaveBeenCalledWith("Recovered", 1);
});
it("keeps the original base revision after a reload of a conflicted draft", async () => {
  localStorage.setItem("worklog:test:draft:test-old-base", "Local work");
  localStorage.setItem("worklog:test:draft-base:test-old-base", "2");
  const save = vi.fn().mockRejectedValue({ status: 409 });
  render(
    <DraftField
      draftKey="test-old-base"
      label="Kept draft"
      initial="Newer server work"
      revision={7}
      save={save}
    />,
    { wrapper },
  );
  await userEvent.click(screen.getByRole("button", { name: "Save now" }));
  await screen.findByText(/Conflict — draft kept/);
  expect(save).toHaveBeenCalledWith("Local work", 2);
});
it("serializes different fields of the same record using acknowledged revisions", async () => {
  const title = vi.fn().mockImplementation(async () => {
    await new Promise((resolve) => setTimeout(resolve, 40));
    return { revision: 2 };
  });
  const body = vi.fn().mockResolvedValue({ revision: 3 });
  render(
    <>
      <DraftField
        draftKey="note-title-shared"
        label="Title"
        initial=""
        revision={1}
        save={title}
        multiline={false}
      />
      <DraftField
        draftKey="note-shared"
        label="Body"
        initial=""
        revision={1}
        save={body}
      />
    </>,
    { wrapper },
  );
  await userEvent.type(screen.getByLabelText("Title"), "A");
  await userEvent.type(screen.getByLabelText("Body"), "B");
  await userEvent.click(screen.getAllByRole("button", { name: "Save now" })[1]);
  await waitFor(() => expect(body).toHaveBeenCalledWith("B", 2));
});
it("does not promote a recovered stale body through a newer sibling title save", async () => {
  localStorage.setItem(
    "worklog:test:draft:note-stale-sibling",
    "Stale local body",
  );
  localStorage.setItem("worklog:test:draft-base:note-stale-sibling", "1");
  const title = vi.fn().mockResolvedValue({ revision: 8 });
  const body = vi.fn().mockRejectedValue({ status: 409 });
  render(
    <>
      <DraftField
        draftKey="note-title-stale-sibling"
        label="Fresh title"
        initial="Server title"
        revision={7}
        save={title}
        multiline={false}
      />
      <DraftField
        draftKey="note-stale-sibling"
        label="Stale body"
        initial="Externally changed body"
        revision={7}
        save={body}
      />
    </>,
    { wrapper },
  );
  await userEvent.type(screen.getByLabelText("Fresh title"), " edit");
  await userEvent.click(screen.getAllByRole("button", { name: "Save now" })[0]);
  await waitFor(() =>
    expect(title).toHaveBeenCalledWith("Server title edit", 7),
  );
  await userEvent.click(screen.getAllByRole("button", { name: "Save now" })[1]);
  await screen.findByText(/Conflict — draft kept/);
  expect(body).toHaveBeenCalledWith("Stale local body", 1);
  expect(localStorage.getItem("worklog:test:draft:note-stale-sibling")).toBe(
    "Stale local body",
  );
});
it("preserves revision zero on a recovered reflection draft", async () => {
  localStorage.setItem(
    "worklog:test:draft:reflection-new-day",
    "Unsent reflection",
  );
  localStorage.setItem("worklog:test:draft-base:reflection-new-day", "0");
  const save = vi.fn().mockRejectedValue({ status: 409 });
  render(
    <DraftField
      draftKey="reflection-new-day"
      label="Reflection"
      initial="Other tab reflection"
      revision={7}
      save={save}
    />,
    { wrapper },
  );
  await userEvent.click(screen.getByRole("button", { name: "Save now" }));
  await screen.findByText(/Conflict — draft kept/);
  expect(save).toHaveBeenCalledWith("Unsent reflection", 0);
  expect(localStorage.getItem("worklog:test:draft:reflection-new-day")).toBe(
    "Unsent reflection",
  );
});
it("isolates unsaved same-date reflection drafts between datasets", async () => {
  setDataContext("dataset-A");
  const save = vi.fn().mockResolvedValue({ revision: 2 });
  const first = render(
    <DraftField
      draftKey="reflection-2026-10-28"
      label="Reflection A"
      initial=""
      revision={1}
      save={save}
    />,
    { wrapper },
  );
  await userEvent.type(screen.getByLabelText("Reflection A"), "Only for A");
  first.unmount();
  setDataContext("dataset-B");
  render(
    <DraftField
      draftKey="reflection-2026-10-28"
      label="Reflection B"
      initial="Dataset B canonical"
      revision={1}
      save={save}
    />,
    { wrapper },
  );
  expect(screen.getByLabelText("Reflection B")).toHaveValue(
    "Dataset B canonical",
  );
  await userEvent.click(screen.getByRole("button", { name: "Save now" }));
  expect(save).not.toHaveBeenCalled();
  expect(
    localStorage.getItem("worklog:dataset-A:draft:reflection-2026-10-28"),
  ).toBe("Only for A");
});
it("does not send an old dataset draft that was queued behind another save", async () => {
  setDataContext("queued-A");
  let finish!: (value: { revision: number }) => void;
  const title = vi.fn().mockImplementation(
    () =>
      new Promise<{ revision: number }>((resolve) => {
        finish = resolve;
      }),
  );
  const body = vi.fn().mockResolvedValue({ revision: 3 });
  render(
    <>
      <DraftField
        draftKey="note-title-queued"
        label="Queued title"
        initial=""
        revision={1}
        save={title}
        multiline={false}
      />
      <DraftField
        draftKey="note-queued"
        label="Queued body"
        initial=""
        revision={1}
        save={body}
      />
    </>,
    { wrapper },
  );
  await userEvent.type(screen.getByLabelText("Queued title"), "A title");
  await userEvent.type(screen.getByLabelText("Queued body"), "A body");
  await userEvent.click(screen.getAllByRole("button", { name: "Save now" })[1]);
  await waitFor(() => expect(title).toHaveBeenCalledTimes(1));
  setDataContext("queued-B");
  finish({ revision: 2 });
  await screen.findByText(/Conflict — draft kept/);
  expect(body).not.toHaveBeenCalled();
  expect(localStorage.getItem("worklog:queued-A:draft:note-queued")).toBe(
    "A body",
  );
});
it("retains typing in memory when draft persistence hits quota and still saves successfully", async () => {
  const write = vi
    .spyOn(Storage.prototype, "setItem")
    .mockImplementation(() => {
      throw new DOMException("Quota exceeded", "QuotaExceededError");
    });
  const remove = vi
    .spyOn(Storage.prototype, "removeItem")
    .mockImplementation(() => {
      throw new DOMException("Storage denied", "SecurityError");
    });
  const save = vi.fn().mockResolvedValue({ revision: 2 });
  try {
    render(
      <DraftField
        draftKey="quota-failure"
        label="Quota note"
        initial=""
        revision={1}
        save={save}
      />,
      { wrapper },
    );
    await userEvent.type(screen.getByLabelText("Quota note"), "Latest thought");
    expect(screen.getByLabelText("Quota note")).toHaveValue("Latest thought");
    expect(
      screen.getByText(/Browser draft storage unavailable/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save now" }));
    await screen.findByText("Saved");
    expect(save).toHaveBeenCalledWith("Latest thought", 1);
    expect(screen.queryByText(/Browser draft storage unavailable/)).toBeNull();
  } finally {
    write.mockRestore();
    remove.mockRestore();
  }
});
