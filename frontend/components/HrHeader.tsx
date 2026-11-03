"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { logout } from "@/lib/api";
import WorkerControl from "./WorkerControl";

export default function HrHeader() {
  const router = useRouter();
  const signOut = useMutation({
    mutationFn: logout,
    onSettled: () => router.replace("/hr/login"),
  });

  return (
    <header className="row" style={{ justifyContent: "space-between" }}>
      <Link href="/hr">← Roles</Link>
      <span className="mock">MOCK SIGN-IN · demo only</span>
      <WorkerControl />
      <button onClick={() => signOut.mutate()} disabled={signOut.isPending}>Sign out</button>
    </header>
  );
}
