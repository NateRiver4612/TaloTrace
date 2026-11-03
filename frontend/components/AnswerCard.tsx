"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { HrAnswer, errMsg, overrideAnswer, retryJob } from "@/lib/api";
import { overrideSchema, type OverrideInput } from "@/lib/schemas";
import { queryKeys } from "@/lib/queryKeys";
import StatusBadge from "./StatusBadge";

export default function AnswerCard({ answer: a, roleId }: { answer: HrAnswer; roleId: string }) {
  const queryClient = useQueryClient();
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const form = useForm<OverrideInput>({
    resolver: zodResolver(overrideSchema),
    defaultValues: { score: 3, reason: "" },
  });
  const reasonValue = useWatch({ control: form.control, name: "reason" });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.candidates(roleId) });

  const saveOverride = useMutation({
    mutationFn: (values: OverrideInput) => overrideAnswer(a.id, values.score, values.reason),
    onSuccess: () => { setMsg({ kind: "ok", text: "Override saved." }); invalidate(); },
    onError: (e) => setMsg({ kind: "err", text: errMsg(e) }),
  });

  const retry = useMutation({
    mutationFn: () => retryJob(a.job.id),
    onSuccess: () => { setMsg({ kind: "ok", text: "Queued for another try." }); invalidate(); },
    onError: (e) => setMsg({ kind: "err", text: errMsg(e) }),
  });

  const busy = saveOverride.isPending || retry.isPending;
  const canRetry = a.counts && (a.job.status === "failed" || a.job.status === "needs_person");
  const latest = a.overrides[0];

  return (
    <div className={`card ${a.counts ? "" : "superseded"}`}>
      <div className="row">
        <strong>Q{a.question_position}</strong>
        <StatusBadge status={a.job.status} />
        {a.counts ? <span className="muted">Counts</span> : <span className="muted">Superseded: a newer answer counts instead</span>}
        <span className="muted">{new Date(a.submitted_at).toLocaleString()}</span>
      </div>
      <p className="muted">{a.question_text}</p>

      {a.kind === "text" ? <p style={{ whiteSpace: "pre-wrap" }}>{a.text}</p> : (
        <>
          <p>🎤 Audio answer received</p>
          <audio controls preload="none" src={`/api/hr/answers/${a.id}/audio`} />
          {a.transcript
            ? <p style={{ whiteSpace: "pre-wrap" }}>
                <span className="muted">Transcript ({a.transcript.engine}){a.transcript.engine === "mock" && <> <span className="mock">MOCK TRANSCRIPT</span></>}:</span><br />{a.transcript.text}
              </p>
            : <p className="muted">No transcript yet.</p>}
        </>
      )}

      {a.job.status === "queued" && <p className="muted">Queued — waiting for the scoring worker.</p>}
      {a.job.status === "failed" && (
        <div className="err">Scoring failed after {a.job.attempts} attempt(s): {a.job.error ?? "unknown error"}. <strong>This is not a low score.</strong> Nothing was scored.</div>
      )}
      {a.job.status === "needs_person" && <div className="warn">Needs a person: {a.job.needs_person_reason ?? "see below"}</div>}

      {a.suggestion && (
        <div>
          <strong>Suggested score: {a.suggestion.score}</strong>{" "}
          <span className="muted">({a.suggestion.model}{a.job.status === "needs_person" ? ", not trusted" : ""})</span>
          {a.suggestion.injection_flag && <div className="warn">⚠ This answer seems to contain instructions aimed at the scorer.</div>}
          <p>{a.suggestion.reason}</p>
        </div>
      )}

      <p><strong>Final score: {a.final_score ?? "not scored yet"}</strong>{latest && <span className="muted"> (HR override by {latest.hr_user})</span>}</p>

      {a.overrides.length > 0 && (
        <details>
          <summary>Override history ({a.overrides.length})</summary>
          <ul>{a.overrides.map((o) => <li key={o.id}>{o.score}, “{o.reason}” by {o.hr_user}, {new Date(o.created_at).toLocaleString()}</li>)}</ul>
        </details>
      )}

      {a.counts && (
        <>
          <form onSubmit={form.handleSubmit((values) => saveOverride.mutate(values))}>
            <div className="row">
              <label>Your score{" "}
                <select {...form.register("score", { valueAsNumber: true })} disabled={busy}>
                  {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <input type="text" placeholder="Reason (required)" {...form.register("reason")} disabled={busy} style={{ flex: 1 }} />
              <button type="submit" disabled={busy || !reasonValue.trim()}>Save override</button>
              {canRetry && <button type="button" disabled={busy} onClick={() => retry.mutate()}>Retry scoring</button>}
            </div>
          </form>
          {form.formState.errors.reason && <div className="err" role="alert">{form.formState.errors.reason.message}</div>}
        </>
      )}
      {msg && <div className={msg.kind === "ok" ? "ok" : "err"} role="status">{msg.text}</div>}
    </div>
  );
}
