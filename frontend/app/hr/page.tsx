"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import HrHeader from "@/components/HrHeader";
import { ApiError, errMsg, getRoles } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";

export default function HrRoles() {
  const router = useRouter();
  const { data: roles, isPending, error } = useQuery({
    queryKey: queryKeys.roles(),
    queryFn: getRoles,
  });
  const isUnauthorized = error instanceof ApiError && error.status === 401;

  useEffect(() => {
    if (isUnauthorized) router.replace("/hr/login");
  }, [isUnauthorized, router]);

  return (
    <main>
      <HrHeader />
      <h1>Open roles</h1>
      {error && !isUnauthorized && <div className="err">{errMsg(error)}</div>}
      {isPending && <p>Loading…</p>}
      {roles?.length === 0 && <p>No roles yet. Run the seed script.</p>}
      {roles?.map((r) => (
        <div className="card" key={r.id}>
          <Link href={`/hr/roles/${r.id}`}><strong>{r.title}</strong></Link>
          <span className="muted"> · {r.candidate_count} candidate{r.candidate_count === 1 ? "" : "s"}</span>
        </div>
      ))}
    </main>
  );
}
