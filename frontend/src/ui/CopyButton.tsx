import { useEffect } from "react";

interface CommandLikeEvent extends Event {
  command?: string;
  source?: EventTarget | null;
}

const timers = new WeakMap<HTMLButtonElement, number>();
let lastMessage = "";
let listenerInstalled = false;

function legacyCopy(text: string): boolean {
  const previousFocus =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.cssText = "position:fixed;inset-block-start:0;opacity:0";
  document.body.append(field);
  field.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    field.remove();
    previousFocus?.focus({ preventScroll: true });
  }
}

function report(button: HTMLButtonElement, ok: boolean) {
  const tip = button.nextElementSibling;
  const existing = timers.get(button);
  if (existing !== undefined) window.clearTimeout(existing);
  button.removeAttribute("data-copied");
  button.removeAttribute("data-failed");
  let message = ok ? "Copied" : "Copy failed";
  if (message === lastMessage) message += "\u00A0";
  lastMessage = message;
  if (tip instanceof HTMLElement) tip.textContent = "";
  void button.offsetWidth;
  button.setAttribute(ok ? "data-copied" : "data-failed", "");
  window.requestAnimationFrame(() => {
    if (tip instanceof HTMLElement) tip.textContent = message;
  });
  timers.set(
    button,
    window.setTimeout(() => {
      button.removeAttribute("data-copied");
      button.removeAttribute("data-failed");
      if (tip instanceof HTMLElement) tip.textContent = "";
    }, 2000),
  );
}

async function copyTarget(button: HTMLButtonElement, target: HTMLElement) {
  const raw =
    target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement
      ? target.value
      : (target.innerText ?? target.textContent ?? "");
  const text = raw.trim();
  if (!text) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else if (!legacyCopy(text)) {
      throw new Error("Copy failed");
    }
    report(button, true);
  } catch {
    if (legacyCopy(text)) {
      report(button, true);
    } else {
      report(button, false);
    }
  }
}

function ensureCopyListener() {
  if (listenerInstalled || typeof window === "undefined") return;
  listenerInstalled = true;
  if ("CommandEvent" in window) {
    document.addEventListener(
      "command",
      (event) => {
        const cmd = event as CommandLikeEvent;
        if (
          cmd.command === "--copy" &&
          cmd.source instanceof HTMLButtonElement &&
          cmd.target instanceof HTMLElement
        ) {
          void copyTarget(cmd.source, cmd.target);
        }
      },
      true,
    );
  } else {
    document.addEventListener("click", (event) => {
      if (event.defaultPrevented) return;
      const origin = event.target;
      if (!(origin instanceof Element)) return;
      const button = origin.closest<HTMLButtonElement>(
        'button[command="--copy"]',
      );
      if (!button) return;
      const targetId = button.getAttribute("commandfor");
      if (!targetId) return;
      const target = document.getElementById(targetId);
      if (target) void copyTarget(button, target);
    });
  }
}

/**
 * Accessible copy button driven by Invoker Commands (`command="--copy"` + `commandfor`),
 * with a single non-duplicating click fallback where `CommandEvent` is absent.
 */
export function CopyButton({
  targetId,
  label,
  className = "quiet",
}: {
  targetId: string;
  label: string;
  className?: string;
}) {
  useEffect(() => {
    ensureCopyListener();
  }, []);
  const invokerProps = {
    command: "--copy",
    commandfor: targetId,
  } as Record<string, string>;
  return (
    <span className="copy">
      <button
        type="button"
        className={`copy-button ${className}`.trim()}
        {...invokerProps}
      >
        {label}
      </button>
      <span className="copy-tip" role="status" />
    </span>
  );
}
