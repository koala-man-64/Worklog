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
export const sessionStore = store("sessionStorage");
