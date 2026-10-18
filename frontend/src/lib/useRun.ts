import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamRun } from "./api";
import type { RunEvent, RunResult } from "./types";

export type RunStatus = "idle" | "running" | "done" | "error";

export interface RunState {
  runId: string | null;
  status: RunStatus;
  events: RunEvent[];
  result: RunResult | null;
  error: string | null;
}

const EMPTY: RunState = { runId: null, status: "idle", events: [], result: null, error: null };

export function useRun() {
  const [state, setState] = useState<RunState>(EMPTY);
  const stopRef = useRef<(() => void) | null>(null);

  const follow = useCallback((runId: string) => {
    stopRef.current?.();
    setState({ ...EMPTY, runId, status: "running" });
    stopRef.current = streamRun(
      runId,
      (event) =>
        setState((s) => (s.runId !== runId || s.events.some((e) => e.seq === event.seq)
          ? s : { ...s, events: [...s.events, event] })),
      async () => {
        // the result is stored right after run_finished; poll briefly for it
        for (let i = 0; i < 40; i++) {
          try {
            const rec = await api.run(runId);
            if (rec.status !== "running") {
              setState((s) => (s.runId !== runId ? s : {
                ...s, status: rec.status === "done" ? "done" : "error", result: rec.result, error: rec.error,
              }));
              return;
            }
          } catch (err) {
            setState((s) => (s.runId !== runId ? s : { ...s, status: "error", error: String(err) }));
            return;
          }
          await new Promise((r) => setTimeout(r, 250));
        }
        setState((s) => (s.runId !== runId ? s : { ...s, status: "error", error: "Timed out waiting for the result" }));
      },
    );
  }, []);

  const start = useCallback(async (tenant: string, pr: number, mode: string) => {
    try {
      const { run_id } = await api.startRun(tenant, pr, mode);
      follow(run_id);
      return run_id;
    } catch (err) {
      setState({ ...EMPTY, status: "error", error: err instanceof Error ? err.message : String(err) });
      return null;
    }
  }, [follow]);

  const clear = useCallback(() => {
    stopRef.current?.();
    setState(EMPTY);
  }, []);

  useEffect(() => () => stopRef.current?.(), []);
  return { ...state, start, follow, clear };
}
