import { useEffect, useRef, useState, type RefObject } from "react";
/**
 * A separator that sets the width of `panel` by pointer drag or arrow keys.
 * Every change starts from the panel's rendered width, so a width the layout
 * clamped never leaves the handle out of step with the pointer. A panel on the
 * handle's right grows as the handle moves left.
 */
export function ResizeHandle({
  label,
  panel,
  min,
  max,
  onResize,
  onReset,
  side = "left",
}: {
  label: string;
  panel: RefObject<HTMLElement | null>;
  min: number;
  /** The widest the layout can show the panel right now. */
  max: () => number;
  /** `done` is false while a drag is still moving. */
  onResize: (width: number, done: boolean) => void;
  onReset: () => void;
  /** Which side of the handle the panel sits on. */
  side?: "left" | "right";
}) {
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; from: number; max: number; to?: number }>(
    null,
  );
  const [dragging, setDragging] = useState(false);
  const [size, setSize] = useState({ now: 0, max: 0 });
  useEffect(() => {
    const target = panel.current;
    const container = handle.current?.parentElement;
    if (!target || !container || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() =>
      setSize({
        now: Math.round(target.getBoundingClientRect().width),
        max: Math.round(max()),
      }),
    );
    observer.observe(target);
    observer.observe(container);
    return () => observer.disconnect();
  }, [panel]);
  useEffect(
    () => () => document.documentElement.classList.remove("resizing"),
    [],
  );
  const fit = (width: number, limit: number) =>
    Math.round(Math.min(Math.max(width, min), Math.max(min, limit)));
  const current = () => panel.current?.getBoundingClientRect().width ?? min;
  // Moving the handle right widens a panel on its left and narrows one on its right.
  const grow = side === "left" ? 1 : -1;
  const end = () => {
    const last = drag.current;
    drag.current = null;
    setDragging(false);
    document.documentElement.classList.remove("resizing");
    if (last?.to !== undefined) onResize(last.to, true);
  };
  return (
    <div
      ref={handle}
      className={dragging ? "resize-handle dragging" : "resize-handle"}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={size.now || undefined}
      aria-valuemin={min}
      aria-valuemax={size.max ? Math.max(min, size.max) : undefined}
      tabIndex={0}
      title="Drag to resize. Double-click to reset."
      onKeyDown={(e) => {
        const next =
          e.key === "ArrowLeft"
            ? current() - 16 * grow
            : e.key === "ArrowRight"
              ? current() + 16 * grow
              : e.key === "Home"
                ? min
                : e.key === "End"
                  ? max()
                  : null;
        if (next === null) return;
        e.preventDefault();
        onResize(fit(next, max()), true);
      }}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture?.(e.pointerId);
        drag.current = { x: e.clientX, from: current(), max: max() };
        setDragging(true);
        document.documentElement.classList.add("resizing");
      }}
      onPointerMove={(e) => {
        const active = drag.current;
        if (!active) return;
        active.to = fit(
          active.from + (e.clientX - active.x) * grow,
          active.max,
        );
        onResize(active.to, false);
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onDoubleClick={onReset}
    />
  );
}
