export type JobStatus = "queued" | "scored" | "failed" | "needs_person";

export interface InterviewState {
  candidate_name: string; total_questions: number; answered: number; complete: boolean;
  current_question: { id: number; position: number; text: string } | null;
}
export interface SubmitResult {
  status: "received"; answer_id: number; duplicate: boolean;
  answered: number; total_questions: number; complete: boolean;
}
export interface HrOverride { id: number; score: number; reason: string; hr_user: string; created_at: string }
export interface HrAnswer {
  id: number; question_id: number; question_position: number; question_text: string;
  kind: "text" | "audio"; text: string | null; has_audio: boolean;
  transcript: { text: string; engine: string } | null;
  counts: boolean; submitted_at: string;
  job: { id: number; status: JobStatus; attempts: number; error: string | null; needs_person_reason: string | null };
  suggestion: { score: number; reason: string; injection_flag: boolean; model: string } | null;
  overrides: HrOverride[]; final_score: number | null;
}
export interface HrCandidate { id: number; name: string; answered: number; total_questions: number; answers: HrAnswer[] }
export interface HrRole { id: number; title: string; candidate_count: number }


export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export const errMsg = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong");


async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { ...init, cache: "no-store", credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch { /* keep the default message */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}


const json = (body: unknown): RequestInit => ({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});


// ---- candidate ----
export const getInterview = (token: string) =>
  request<InterviewState>(`/api/v1/interview/${encodeURIComponent(token)}`);


export function submitAnswer(token: string, questionId: number, payload: { text: string } | { audio: File }) {
  const form = new FormData();                       // no Content-Type header: the browser adds the boundary
  form.append("question_id", String(questionId));
  if ("text" in payload) form.append("text", payload.text);
  else form.append("audio", payload.audio);
  return request<SubmitResult>(`/api/v1/interview/${encodeURIComponent(token)}/answers`, { method: "POST", body: form });
}


// ---- HR ----
export const login = (username: string, password: string) => request<{ username: string }>("/api/hr/login", json({ username, password }));
export const logout = () => request<{ ok: boolean }>("/api/hr/logout", { method: "POST" });
export const getRoles = () => request<HrRole[]>("/api/hr/roles");
export const getCandidates = (roleId: string) => request<HrCandidate[]>(`/api/hr/roles/${roleId}/candidates`);
export const overrideAnswer = (answerId: number, score: number, reason: string) =>
  request<HrOverride>(`/api/hr/answers/${answerId}/override`, json({ score, reason }));
export const retryJob = (jobId: number) => request<unknown>(`/api/hr/jobs/${jobId}/retry`, { method: "POST" });


// ---- worker ----
export interface WorkerStatus {
  running: boolean; pending: number; configured: boolean; last_error: string | null;
}
export const getWorkerStatus = () => request<WorkerStatus>("/api/hr/worker/status");
export const runWorker = () => request<{ status: "started" | "already_running" }>("/api/hr/worker/run", { method: "POST" });
