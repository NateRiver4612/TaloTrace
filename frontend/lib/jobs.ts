import { HrCandidate } from "./api";

// Polling should only continue while at least one current answer is still queued.
// The backend folds its internal "running" state into "queued", so checking
// "queued" covers both waiting and in-progress jobs.
export const hasQueuedJobs = (candidates: HrCandidate[] | undefined): boolean =>
  candidates?.some((c) => c.answers.some((a) => a.counts && a.job.status === "queued")) ?? false;
