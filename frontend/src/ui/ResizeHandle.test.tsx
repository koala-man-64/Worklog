import { useRef } from "react";
import { expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResizeHandle } from "./ResizeHandle";
import { useStoredWidth } from "../hooks";
// jsdom has no layout, so the panel reports the width its style asks for.
function ResizablePanel() {
  const panel = useRef<HTMLDivElement>(null);
  const stored = useStoredWidth("test-width", 200, 400);
  return (
    <div>
      <div
        data-testid="panel"
        ref={(el) => {
          panel.current = el;
          if (el)
            el.getBoundingClientRect = () =>
              ({ width: parseFloat(el.style.width) || 250 }) as DOMRect;
        }}
        style={{ width: stored.width ?? undefined }}
      />
      <ResizeHandle
        label="Resize panel"
        panel={panel}
        min={200}
        max={() => 400}
        onResize={stored.resize}
        onReset={stored.reset}
      />
    </div>
  );
}
it("resizes a panel from the keyboard within its limits and remembers it", async () => {
  render(<ResizablePanel />);
  const handle = screen.getByRole("separator", { name: "Resize panel" });
  const panel = screen.getByTestId("panel");
  handle.focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(panel.style.width).toBe("266px");
  expect(localStorage.getItem("test-width")).toBe("266");
  await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
  expect(panel.style.width).toBe("234px");
  await userEvent.keyboard("{Home}");
  expect(panel.style.width).toBe("200px");
  await userEvent.keyboard("{ArrowLeft}");
  expect(panel.style.width).toBe("200px");
  await userEvent.keyboard("{End}");
  expect(localStorage.getItem("test-width")).toBe("400");
});
it("follows a drag live and stores the width only when the drag ends", () => {
  render(<ResizablePanel />);
  const handle = screen.getByRole("separator", { name: "Resize panel" });
  const panel = screen.getByTestId("panel");
  fireEvent.pointerDown(handle, { button: 0, clientX: 100, pointerId: 1 });
  expect(document.documentElement).toHaveClass("resizing");
  fireEvent.pointerMove(handle, { clientX: 180, pointerId: 1 });
  expect(panel.style.width).toBe("330px");
  expect(localStorage.getItem("test-width")).toBeNull();
  fireEvent.pointerMove(handle, { clientX: 900, pointerId: 1 });
  expect(panel.style.width).toBe("400px");
  fireEvent.pointerUp(handle, { clientX: 900, pointerId: 1 });
  expect(localStorage.getItem("test-width")).toBe("400");
  expect(document.documentElement).not.toHaveClass("resizing");
  // Moves after the drag ended change nothing.
  fireEvent.pointerMove(handle, { clientX: 100, pointerId: 1 });
  expect(panel.style.width).toBe("400px");
});
it("stores nothing for a click and resets to the layout width on double-click", () => {
  localStorage.setItem("test-width", "320");
  render(<ResizablePanel />);
  const handle = screen.getByRole("separator", { name: "Resize panel" });
  const panel = screen.getByTestId("panel");
  expect(panel.style.width).toBe("320px");
  fireEvent.pointerDown(handle, { button: 0, clientX: 100, pointerId: 1 });
  fireEvent.pointerUp(handle, { clientX: 100, pointerId: 1 });
  expect(localStorage.getItem("test-width")).toBe("320");
  fireEvent.doubleClick(handle);
  expect(panel.style.width).toBe("");
  expect(localStorage.getItem("test-width")).toBeNull();
});
it("ignores a stored width outside the panel's limits", () => {
  localStorage.setItem("test-width", "9999");
  render(<ResizablePanel />);
  expect(screen.getByTestId("panel").style.width).toBe("");
});
