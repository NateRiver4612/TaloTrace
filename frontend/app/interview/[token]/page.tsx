"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ApiError, errMsg, getInterview, submitAnswer } from "@/lib/api";
import { answerSchema, type AnswerInput } from "@/lib/schemas";
import { queryKeys } from "@/lib/queryKeys";

export default function InterviewPage() {
  const { token } = useParams<{ token: string }>();
  const queryClient = useQueryClient();

  const { data: state, isPending, error, refetch } = useQuery({
    queryKey: queryKeys.interview(token),
    queryFn: () => getInterview(token),
  });

  const form = useForm<AnswerInput>({
    resolver: zodResolver(answerSchema),
    defaultValues: { mode: "text", text: "", audio: null },
  });
  const mode = useWatch({ control: form.control, name: "mode" });

  const [received, setReceived] = useState<string | null>(null);
  const [fileKey, setFileKey] = useState(0);

  const submit = useMutation({
    mutationFn: (vars: { questionId: number; payload: { text: string } | { audio: File } }) =>
      submitAnswer(token, vars.questionId, vars.payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.interview(token) }),
  });

  const onSubmit = form.handleSubmit(async (values) => {
    const q = state?.current_question;
    if (!q) return;
    try {
      await submit.mutateAsync({
        questionId: q.id,
        payload: values.mode === "text" ? { text: values.text } : { audio: values.audio! },
      });
      setReceived(`Your answer to question ${q.position} was received.`);
      form.reset({ mode: values.mode, text: "", audio: null });
      setFileKey((k) => k + 1);
    } catch (e) {
      form.setError("root", {
        type: "server",
        message: `We could not send your answer: ${errMsg(e)} Your answer is still here. Please try again.`,
      });
    }
  });

  const notFound = error instanceof ApiError && error.status === 404;

  if (notFound)
    return (
      <main>
        <h1>Link not found</h1>
        <p>This interview link is not valid. Please check the link in your email.</p>
      </main>
    );

  if (isPending) return <main><p>Loading…</p></main>;

  if (error || !state)
    return (
      <main>
        <div className="err">
          {errMsg(error)} <button onClick={() => refetch()}>Retry</button>
        </div>
      </main>
    );

  const q = state.current_question;

  return (
    <main>
      <h1>Hello, {state.candidate_name}</h1>
      {received && <div className="ok" role="status">{received}</div>}

      {state.complete || !q ? (
        <>
          <h2>All done. Thank you!</h2>
          <p>All {state.total_questions} answers have been received. You can close this page.</p>
        </>
      ) : (
        <form onSubmit={onSubmit}>
          <p className="muted">Question {q.position} of {state.total_questions}</p>
          <h2>{q.text}</h2>

          <div className="row tabs">
            <button
              type="button"
              aria-pressed={mode === "text"}
              onClick={() => { form.setValue("mode", "text"); form.clearErrors(); }}
              disabled={submit.isPending}
            >
              Type your answer
            </button>
            <button
              type="button"
              aria-pressed={mode === "audio"}
              onClick={() => { form.setValue("mode", "audio"); form.clearErrors(); }}
              disabled={submit.isPending}
            >
              Upload audio
            </button>
          </div>

          {mode === "text" ? (
            <textarea {...form.register("text")} disabled={submit.isPending} aria-label="Your answer" />
          ) : (
            <p>
              <input
                key={fileKey}
                type="file"
                accept=".mp3,.wav,.m4a,.ogg,.webm,.flac,audio/*"
                onChange={(e) => form.setValue("audio", e.target.files?.[0] ?? null, { shouldValidate: true })}
                disabled={submit.isPending}
                aria-label="Audio file"
              />
            </p>
          )}

          {form.formState.errors.text && <div className="err" role="alert">{form.formState.errors.text.message}</div>}
          {form.formState.errors.audio && <div className="err" role="alert">{form.formState.errors.audio.message}</div>}
          {form.formState.errors.root && <div className="err" role="alert">{form.formState.errors.root.message}</div>}

          <p>
            <button type="submit" disabled={submit.isPending}>
              {submit.isPending ? "Sending…" : "Submit answer"}
            </button>
          </p>
        </form>
      )}
    </main>
  );
}
