"use client";

import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { errMsg, login } from "@/lib/api";
import { loginSchema, type LoginInput } from "@/lib/schemas";

export default function HrLogin() {
  const router = useRouter();
  const form = useForm<LoginInput>({
    resolver: zodResolver(loginSchema),
    defaultValues: { username: "", password: "" },
  });
  const signIn = useMutation({
    mutationFn: (values: LoginInput) => login(values.username, values.password),
    onSuccess: () => router.push("/hr"),
  });

  return (
    <main>
      <h1>HR sign-in</h1>
      <p><span className="mock">MOCK SIGN-IN</span> A shared demo account from the server&apos;s environment variables. Not real authentication.</p>
      <form onSubmit={form.handleSubmit((values) => signIn.mutate(values))}>
        <p><input type="text" placeholder="Username" {...form.register("username")} autoComplete="username" /></p>
        <p><input type="password" placeholder="Password" {...form.register("password")} autoComplete="current-password" /></p>
        {(form.formState.errors.username || form.formState.errors.password) && (
          <div className="err" role="alert">
            {form.formState.errors.username?.message ?? form.formState.errors.password?.message}
          </div>
        )}
        {signIn.isError && <div className="err" role="alert">{errMsg(signIn.error)}</div>}
        <button type="submit" disabled={signIn.isPending}>{signIn.isPending ? "Signing in…" : "Sign in"}</button>
      </form>
    </main>
  );
}
