import type { ReactNode } from "react";

// Generated robot avatars that tell agents apart. A name joins a colour, eyes
// and a top, such as teal-visor-antenna; backend/worklog/avatars.py keeps the
// same lists in the same order and decides which names exist.
export const AVATAR_COLORS = {
  red: "#c8463d",
  orange: "#c25e1a",
  amber: "#a8730a",
  lime: "#5c8a1e",
  green: "#2e8049",
  teal: "#1f7a70",
  cyan: "#0d7891",
  sky: "#2f78c4",
  blue: "#3d5cc9",
  indigo: "#5b4cc4",
  violet: "#8a44b8",
  pink: "#b83f7a",
} as const;
export const AVATAR_EYES = [
  "dots",
  "visor",
  "cyclops",
  "sleepy",
  "square",
] as const;
export const AVATAR_TOPS = ["antenna", "twin", "ears", "bare"] as const;

export type AvatarParts = {
  color: keyof typeof AVATAR_COLORS;
  eyes: (typeof AVATAR_EYES)[number];
  top: (typeof AVATAR_TOPS)[number];
};

const COLOR_NAMES = Object.keys(AVATAR_COLORS) as AvatarParts["color"][];
const INK = "#1f2a2e";
const FACE = "#ffffff";
// An agent whose avatar is missing or no longer drawn here.
const UNKNOWN = "#7a827c";

export function avatarParts(name: string | null | undefined) {
  const [color, eyes, top, ...rest] = (name ?? "").split("-");
  const parts = { color, eyes, top } as AvatarParts;
  return COLOR_NAMES.includes(parts.color) &&
    AVATAR_EYES.includes(parts.eyes) &&
    AVATAR_TOPS.includes(parts.top) &&
    rest.length === 0
    ? parts
    : null;
}

export function avatarName({ color, eyes, top }: AvatarParts) {
  return `${color}-${eyes}-${top}`;
}

const EYES: Record<AvatarParts["eyes"], ReactNode> = {
  dots: (
    <>
      <circle cx="12.8" cy="16.5" r="1.7" />
      <circle cx="19.2" cy="16.5" r="1.7" />
    </>
  ),
  visor: <rect x="10.5" y="15" width="11" height="3.2" rx="1.6" />,
  cyclops: (
    <>
      <circle cx="16" cy="16.6" r="2.6" />
      <circle cx="16.9" cy="15.7" r="0.8" fill={FACE} />
    </>
  ),
  sleepy: (
    <path
      d="M11 16.8h3.4M17.6 16.8H21"
      fill="none"
      stroke={INK}
      strokeWidth={1.6}
      strokeLinecap="round"
    />
  ),
  square: (
    <>
      <rect x="11.2" y="15" width="3.2" height="3.2" rx="0.6" />
      <rect x="17.6" y="15" width="3.2" height="3.2" rx="0.6" />
    </>
  ),
};

const TOPS: Record<AvatarParts["top"], ReactNode> = {
  antenna: (
    <>
      <path d="M16 11V7.6" stroke={FACE} strokeWidth={1.6} />
      <circle cx="16" cy="6.5" r="1.8" />
    </>
  ),
  twin: (
    <>
      <path
        d="M12.2 11l-1.4-3.2M19.8 11l1.4-3.2"
        stroke={FACE}
        strokeWidth={1.6}
      />
      <circle cx="10.5" cy="7.3" r="1.4" />
      <circle cx="21.5" cy="7.3" r="1.4" />
    </>
  ),
  ears: (
    <>
      <rect x="5.6" y="14.5" width="3" height="6" rx="1.2" />
      <rect x="23.4" y="14.5" width="3" height="6" rx="1.2" />
    </>
  ),
  bare: null,
};

/** Decorative: the name beside it says who it is. */
export function Avatar({
  name,
  kind = "agent",
  size = 24,
}: {
  name?: string | null;
  kind?: "agent" | "human";
  size?: number;
}) {
  const svg = (className: string, body: ReactNode) => (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden="true"
      focusable="false"
    >
      {body}
    </svg>
  );
  if (kind === "human")
    return svg(
      "avatar human",
      <>
        <circle className="avatar-human-bg" cx="16" cy="16" r="16" />
        <g className="avatar-human-figure">
          <circle cx="16" cy="12.5" r="4.6" />
          <path d="M7.5 25.5a8.5 8.5 0 0 1 17 0z" />
        </g>
      </>,
    );
  const parts = avatarParts(name);
  const color = parts ? AVATAR_COLORS[parts.color] : UNKNOWN;
  return svg(
    "avatar",
    <>
      <circle cx="16" cy="16" r="16" fill={color} />
      <g fill={FACE}>{TOPS[parts?.top ?? "bare"]}</g>
      <rect x="8" y="11" width="16" height="13" rx="3.5" fill={FACE} />
      <g fill={INK}>{EYES[parts?.eyes ?? "dots"]}</g>
      <rect x="13.5" y="20.3" width="5" height="1.4" rx="0.7" fill={color} />
    </>,
  );
}
