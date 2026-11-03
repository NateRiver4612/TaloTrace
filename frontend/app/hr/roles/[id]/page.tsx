"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import AnswerCard from "@/components/AnswerCard";
import HrHeader from "@/components/HrHeader";
import { ApiError, errMsg, getCandidates } from "@/lib/api";
import { hasQueuedJobs } from "@/lib/jobs";
import { queryKeys } from "@/lib/queryKeys";

export default function RolePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: candidates, isPending, error, refetch } = useQuery({
    queryKey: queryKeys.candidates(id),
    queryFn: () => getCandidates(id),
    refetchInterval: (query) => (hasQueuedJobs(query.state.data) ? 5000 : false),
  });
  const isUnauthorized = error instanceof ApiError && error.status === 401;

  useEffect(() => {
    if (isUnauthorized) router.replace("/hr/login");
  }, [isUnauthorized, router]);

  return (
    <main>
      <HrHeader />
      <h1>Candidates</h1>
      {error && !isUnauthorized && <div className="err">{errMsg(error)} <button onClick={() => refetch()}>Retry</button></div>}
      {isPending && <p>Loading…</p>}
      {candidates?.length === 0 && <p>No candidates for this role yet.</p>}
      {candidates?.map((c) => {
        const attention = c.answers.filter((a) => a.counts && (a.job.status === "failed" || a.job.status === "needs_person")).length;
        return (
          <section key={c.id}>
            <h2>{c.name}</h2>
            <p className="muted">
              {c.answered}/{c.total_questions} questions answered
              {attention > 0 && <> · <strong>{attention} need{attention === 1 ? "s" : ""} attention</strong></>}
            </p>
            {c.answers.length === 0 && <p className="muted">No answers yet.</p>}
            {c.answers.map((a) => <AnswerCard key={a.id} answer={a} roleId={id} />)}
          </section>
        );
      })}
    </main>
  );
}
