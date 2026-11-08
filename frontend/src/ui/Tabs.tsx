import {
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
export type TabSpec = {
  id: string;
  label: ReactNode;
  /** The tab's name in words, where its label shows something shorter, such as "1/2". */
  name?: string;
  /** Drawn when the tab first opens, then kept, hidden while another tab shows. */
  panel: () => ReactNode;
};
/**
 * Tabs in the WAI-ARIA pattern: the arrow keys, Home and End move between them and open the
 * one reached. A panel is drawn the first time its tab opens and kept after, so its drafts
 * and half-filled forms survive a switch, and a panel that reads data reads it only once opened.
 */
export function Tabs({
  label,
  tabs,
  className,
}: {
  label: string;
  tabs: TabSpec[];
  className?: string;
}) {
  const base = useId();
  const [selected, setSelected] = useState(tabs[0]?.id);
  const [opened, setOpened] = useState(() => new Set([tabs[0]?.id]));
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const open = (id: string) => {
    setSelected(id);
    setOpened((was) => (was.has(id) ? was : new Set(was).add(id)));
  };
  const step = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = tabs.length - 1;
    const next =
      e.key === "ArrowRight"
        ? index === last
          ? 0
          : index + 1
        : e.key === "ArrowLeft"
          ? index === 0
            ? last
            : index - 1
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? last
              : null;
    if (next === null) return;
    e.preventDefault();
    open(tabs[next].id);
    buttons.current.get(tabs[next].id)?.focus();
  };
  return (
    <div className={"tabs" + (className ? " " + className : "")}>
      <div className="tab-list" role="tablist" aria-label={label}>
        {tabs.map((tab, index) => (
          <button
            key={tab.id}
            ref={(node) => {
              if (node) buttons.current.set(tab.id, node);
              else buttons.current.delete(tab.id);
            }}
            type="button"
            role="tab"
            id={`${base}-tab-${tab.id}`}
            aria-label={tab.name}
            aria-selected={tab.id === selected}
            aria-controls={`${base}-panel-${tab.id}`}
            tabIndex={tab.id === selected ? 0 : -1}
            onClick={() => open(tab.id)}
            onKeyDown={(e) => step(e, index)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.id}
          className="tab-panel"
          role="tabpanel"
          id={`${base}-panel-${tab.id}`}
          aria-labelledby={`${base}-tab-${tab.id}`}
          hidden={tab.id !== selected}
          tabIndex={0}
        >
          {opened.has(tab.id) && tab.panel()}
        </div>
      ))}
    </div>
  );
}
