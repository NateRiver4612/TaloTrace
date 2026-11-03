import { JobStatus } from "@/lib/api";

const LABEL: Record<JobStatus, string> = {
  queued: "Queued", scored: "Scored", failed: "Failed", needs_person: "Needs a person",
};

export default function StatusBadge({ status }: { status: JobStatus }) {
  return <span className={`badge badge-${status}`}>{LABEL[status]}</span>;
}
