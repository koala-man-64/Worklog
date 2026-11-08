import { expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Scratchpad } from "./Notes";
import { setDataContext } from "../api";
import { commands } from "../commands";
import type { Note } from "../apiTypes";
import { aNote } from "../fixtures";
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
const note = (fields: Partial<Note>): Note => ({
  ...aNote({ id: "pad", title: "Scratchpad", ownerId: "item" }),
  ...fields,
});
it("creates the scratchpad on its first save and updates that note after", async () => {
  setDataContext("scratchpad-" + crypto.randomUUID());
  const create = vi
    .spyOn(commands, "createNote")
    .mockResolvedValue(note({ text: "First" }));
  const update = vi
    .spyOn(commands, "updateNote")
    .mockResolvedValue(note({ revision: 2 }));
  render(<Scratchpad itemId="item" notes={[]} />, { wrapper });
  const field = screen.getByLabelText("Scratchpad (Markdown)");
  await userEvent.type(field, "First");
  await userEvent.click(screen.getByRole("button", { name: "Save now" }));
  await waitFor(() =>
    expect(create).toHaveBeenCalledWith({
      ownerType: "item",
      ownerId: "item",
      title: "Scratchpad",
      text: "First",
    }),
  );
  // The item has not been read again, so the next save goes to the note the first one made.
  await userEvent.type(field, " and more");
  await userEvent.click(screen.getByRole("button", { name: "Save now" }));
  await waitFor(() =>
    expect(update).toHaveBeenCalledWith("pad", {
      revision: 1,
      text: "First and more",
    }),
  );
  expect(create).toHaveBeenCalledTimes(1);
  create.mockRestore();
  update.mockRestore();
});
it("keeps archived notes readable under the scratchpad, with no way to recover them beside it", () => {
  setDataContext("scratchpad-" + crypto.randomUUID());
  render(
    <Scratchpad
      itemId="item"
      notes={[
        note({ text: "Current thoughts" }),
        note({
          id: "old",
          archived: true,
          title: "Decisions",
          text: "Use **SQLite**",
        }),
      ]}
    />,
    { wrapper },
  );
  expect(screen.getByLabelText("Scratchpad (Markdown)")).toHaveValue(
    "Current thoughts",
  );
  expect(screen.getByText("Archived notes (1)")).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Decisions" }),
  ).toBeInTheDocument();
  expect(screen.getByText("SQLite").tagName).toBe("STRONG");
  expect(screen.queryByRole("button", { name: "Recover" })).toBeNull();
});
