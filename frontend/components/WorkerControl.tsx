"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errMsg, getWorkerStatus, runWorker } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";

export default function WorkerControl() {
  const queryClient = useQueryClient();

  const { data: status, isPending } = useQuery({
    queryKey: queryKeys.worker(),
    queryFn: getWorkerStatus,
    // Only poll while there is work; go quiet once the queue is empty.
    refetchInterval: (query) => {
      const s = query.state.data;
      return s?.running || (s?.pending ?? 0) > 0 ? 3000 : false;
    },
  });

  const run = useMutation({
    mutationFn: runWorker,
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.worker() });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
    },
  });

  if (isPending || !status) return null;

  if (!status.configured) {
    return (
      <span className="muted" title="Set DEEPSEEK_API_KEY in .env and restart the API.">
        Scoring not configured
      </span>
    );
  }

  const pending = status.pending;
  const label = status.running
    ? `Scoring… ${pending} left`
    : pending > 0
      ? `${pending} waiting to be scored`
      : "All answers scored";

  return (
    <div className="row" style={{ gap: 8 }}>
      <span className="muted">{label}</span>
      <button
        type="button"
        onClick={() => run.mutate()}
        disabled={run.isPending || status.running || pending === 0}
      >
        {status.running ? "Scoring…" : "Run scoring"}
      </button>
      {run.isError && (
        <span className="muted" role="alert" style={{ color: "#b00" }}>{errMsg(run.error)}</span>
      )}
      {status.last_error && !status.running && (
        <span className="muted" style={{ color: "#b00" }} title={status.last_error}>Last run failed</span>
      )}
    </div>
  );
}
