import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Pager } from "./Pager";

const button = (name: string) => screen.getByRole("button", { name });

it("has no pager for a list that fits on its first page", () => {
  render(<Pager offset={0} total={50} onChange={() => {}} label="roles" />);
  expect(screen.queryByRole("group", { name: "roles pages" })).toBeNull();
});

it("names the rows that show and moves a page each way", async () => {
  const onChange = vi.fn();
  const { rerender } = render(
    <Pager offset={0} total={120} onChange={onChange} label="roles" />,
  );
  expect(screen.getByRole("group", { name: "roles pages" })).toHaveTextContent(
    "1 to 50 of 120",
  );
  expect(button("Previous roles")).toBeDisabled();
  await userEvent.click(button("Next roles"));
  expect(onChange).toHaveBeenLastCalledWith(50);

  rerender(
    <Pager offset={100} total={120} onChange={onChange} label="roles" />,
  );
  expect(screen.getByRole("group", { name: "roles pages" })).toHaveTextContent(
    "101 to 120 of 120",
  );
  expect(button("Next roles")).toBeDisabled();
  await userEvent.click(button("Previous roles"));
  expect(onChange).toHaveBeenLastCalledWith(50);
});

it("takes the page size its list reads", async () => {
  const onChange = vi.fn();
  render(
    <Pager
      offset={0}
      total={250}
      limit={100}
      onChange={onChange}
      label="work"
    />,
  );
  expect(screen.getByRole("group", { name: "work pages" })).toHaveTextContent(
    "1 to 100 of 250",
  );
  await userEvent.click(button("Next work"));
  expect(onChange).toHaveBeenLastCalledWith(100);
});

it("stays while a later page is open, so a list that shrank can be paged back", () => {
  render(<Pager offset={50} total={30} onChange={() => {}} label="results" />);
  expect(button("Previous results")).toBeEnabled();
  expect(button("Next results")).toBeDisabled();
});

it("has no pager while its list reads no rows, on any page", () => {
  // A list reads as 0 while it loads, so a later page doesn't flash "51 to 0 of 0".
  render(<Pager offset={50} total={0} onChange={() => {}} label="results" />);
  expect(screen.queryByRole("group", { name: "results pages" })).toBeNull();
});
