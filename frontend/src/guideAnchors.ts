/**
 * Heading anchors as GitHub makes them, so a guide link such as `#back-up-and-export`
 * finds the same heading on GitHub and on the Guide page.
 */
export function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, "")
    .replace(/ /g, "-");
}

/** Numbers repeated headings in document order: `notes`, `notes-1`, `notes-2`. */
export function slugger(): (text: string) => string {
  const occurrences = new Map<string, number>();
  return (text) => {
    const original = slug(text);
    let result = original;
    while (occurrences.has(result)) {
      const next = (occurrences.get(original) ?? 0) + 1;
      occurrences.set(original, next);
      result = `${original}-${next}`;
    }
    occurrences.set(result, 0);
    return result;
  };
}

export type Heading = {
  level: number;
  text: string;
  slug: string;
  /** 1-based, as the Markdown parser counts source lines. */
  line: number;
};

/** Every heading outside code blocks, in order, with the anchor the Guide page gives it. */
export function headings(markdown: string): Heading[] {
  const next = slugger();
  const found: Heading[] = [];
  let fenced = false;
  markdown.split("\n").forEach((source, index) => {
    if (/^\s*```/.test(source)) {
      fenced = !fenced;
      return;
    }
    const match = fenced ? null : /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(source);
    if (match) {
      const text = match[2].replace(/[`*]/g, "");
      found.push({
        level: match[1].length,
        text,
        slug: next(text),
        line: index + 1,
      });
    }
  });
  return found;
}
