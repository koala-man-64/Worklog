import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request, contextStorageKey, getDataContext, ApiError } from "./api";
import { localStore } from "./storage";
export function useData<T>(path: string) {
  return useQuery({
    queryKey: [getDataContext(), path],
    queryFn: () => request<T>(path),
  });
}
export function useAction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      path,
      method = "POST",
      body = {},
    }: {
      path: string;
      method?: string;
      body?: unknown;
    }) => request(path, method, body),
    onSuccess: () =>
      client.invalidateQueries({
        predicate: (q) => q.queryKey[0] !== "session",
      }),
  });
}
export function useCommand<TBody, TResult>(
  command: (body: TBody) => Promise<TResult>,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: command,
    onSuccess: () =>
      client.invalidateQueries({
        predicate: (q) => q.queryKey[0] !== "session",
      }),
  });
}
// A per-record serialized queue keeps revisions ordered; drafts survive failures and reloads.
const queues = new Map<string, Promise<unknown>>();
// Advance a draft only through revisions this tab actually acknowledged from its
// own base. An unrelated newer field must never promote a recovered stale draft.
const transitions = new Map<string, Map<number, number>>();
function acknowledgedRevision(recordKey: string, base: number) {
  const seen = new Set<number>();
  const chain = transitions.get(recordKey);
  while (chain?.has(base) && !seen.has(base)) {
    seen.add(base);
    base = chain.get(base)!;
  }
  return base;
}
export function useDraft(
  key: string,
  initial: string,
  revision: number,
  save: (text: string, revision: number) => Promise<{ revision: number }>,
) {
  const client = useQueryClient();
  const contextRef = useRef(getDataContext());
  const dataId = contextRef.current;
  const recordKey = contextStorageKey(
    "record",
    key
      .replace(/^item-(title|description)-/, "item-")
      .replace(/^note-title-/, "note-"),
    dataId,
  );
  const draftKey = contextStorageKey("draft", key, dataId);
  const baseKey = contextStorageKey("draft-base", key, dataId);
  const draftRead = localStore.read(draftKey);
  const baseRead = localStore.read(baseKey);
  const hasDraft = draftRead.value !== null;
  const storedBase = baseRead.value;
  const parsedBase =
    storedBase === null ? (hasDraft ? -1 : revision) : Number(storedBase);
  const base =
    Number.isSafeInteger(parsedBase) && parsedBase >= 0 ? parsedBase : -1;
  const [value, setValue] = useState(() => draftRead.value ?? initial);
  const [storageWarning, setStorageWarning] = useState(
    !draftRead.ok || !baseRead.ok,
  );
  const currentText = useRef(value);
  const [state, setState] = useState(
    !hasDraft
      ? "Saved"
      : base < 0
        ? "Conflict — draft base unavailable. Compare saved value."
        : "Unsaved draft",
  );
  const latest = useRef({ save, revision });
  latest.current.save = save;
  const dirty = useRef(hasDraft);
  const revisionRef = useRef(base);
  const enqueued = useRef<string | null>(null);
  useEffect(() => {
    if (!dirty.current) {
      revisionRef.current = revision;
      setValue(initial);
      currentText.current = initial;
    }
  }, [revision, initial]);
  const flush = () => {
    if (!dirty.current || enqueued.current === value) return;
    if (revisionRef.current < 0) {
      setState("Conflict — draft base unavailable. Compare saved value.");
      return;
    }
    const text = value;
    enqueued.current = text;
    setState("Saving…");
    const pending = (queues.get(recordKey) ?? Promise.resolve())
      .catch(() => undefined)
      .then(async () => {
        if (getDataContext() !== dataId)
          throw new ApiError(
            409,
            "The data folder changed; the original draft is retained.",
            "data_context",
          );
        const base = acknowledgedRevision(recordKey, revisionRef.current);
        const result = await latest.current.save(text, base);
        const chain = transitions.get(recordKey) ?? new Map<number, number>();
        chain.set(base, result.revision);
        transitions.set(recordKey, chain);
        return result;
      });
    queues.set(recordKey, pending);
    pending
      .then((result) => {
        revisionRef.current = result.revision;
        if (currentText.current === text) {
          localStore.remove(draftKey);
          localStore.remove(baseKey);
          dirty.current = false;
          setState("Saved");
          setStorageWarning(false);
        } else {
          if (!localStore.write(baseKey, String(result.revision)))
            setStorageWarning(true);
        }
        client.invalidateQueries({
          predicate: (q) => q.queryKey[0] !== "session",
        });
      })
      .catch((error) => {
        client.invalidateQueries({
          predicate: (q) => q.queryKey[0] !== "session",
        });
        setState(
          error.status === 409
            ? "Conflict — draft kept. Reload to compare."
            : "Save failed — draft kept. Retry.",
        );
      })
      .finally(() => {
        enqueued.current = null;
      });
  };
  useEffect(() => {
    if (!dirty.current) return;
    const timer = setTimeout(flush, 800);
    return () => clearTimeout(timer);
  }, [value]);
  return {
    value,
    state,
    set: (text: string) => {
      setValue(text);
      currentText.current = text;
      if (
        !dirty.current &&
        !localStore.write(baseKey, String(revisionRef.current))
      )
        setStorageWarning(true);
      dirty.current = true;
      if (!localStore.write(draftKey, text)) setStorageWarning(true);
      setState("Unsaved draft");
    },
    flush,
    discard: () => {
      localStore.remove(draftKey);
      localStore.remove(baseKey);
      dirty.current = false;
      revisionRef.current = revision;
      setValue(initial);
      currentText.current = initial;
      setState("Saved");
      setStorageWarning(false);
    },
    rebase: () => {
      revisionRef.current = revision;
      if (!localStore.write(baseKey, String(revision))) setStorageWarning(true);
      flush();
    },
    storageWarning,
  };
}
