/** Browser persistence is best effort. Denied/quota-limited storage must not reject edits. */
function store(kind: "localStorage" | "sessionStorage") {
  const fallback = new Map<string, string | null>();
  return {
    read(key: string): { value: string | null; ok: boolean } {
      if (fallback.has(key))
        return { value: fallback.get(key) ?? null, ok: false };
      try {
        return { value: window[kind].getItem(key), ok: true };
      } catch {
        return { value: null, ok: false };
      }
    },
    write(key: string, value: string) {
      try {
        window[kind].setItem(key, value);
        fallback.delete(key);
        return true;
      } catch {
        fallback.set(key, value);
        return false;
      }
    },
    remove(key: string) {
      try {
        window[kind].removeItem(key);
        fallback.delete(key);
        return true;
      } catch {
        fallback.set(key, null);
        return false;
      }
    },
    keys() {
      try {
        return [...new Set([...Object.keys(window[kind]), ...fallback.keys()])];
      } catch {
        return [...fallback.keys()];
      }
    },
  };
}
export const localStore = store("localStorage");
/** Ids remembered as a JSON array, such as the collapsed nodes of a tree. Bad data reads as empty. */
export function readIdSet(key: string) {
  try {
    const ids: unknown = JSON.parse(localStore.read(key).value ?? "[]");
    return new Set(
      Array.isArray(ids)
        ? ids.filter((i): i is string => typeof i === "string")
        : [],
    );
  } catch {
    return new Set<string>();
  }
}
export const sessionStore = store("sessionStorage");
