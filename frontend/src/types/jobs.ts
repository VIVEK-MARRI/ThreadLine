/* Job + diagnostics contracts. */

export type JobStatus =
  | "PENDING"
  | "RUNNING"
  | "RETRY_WAITING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

export interface JobCounts {
  PENDING?: number;
  RUNNING?: number;
  SUCCEEDED?: number;
  RETRY_WAITING?: number;
  FAILED?: number;
  CANCELLED?: number;
}

export interface JobHealth {
  worker_enabled: boolean;
  counts: JobCounts;
  stale_running: number;
  oldest_pending_age_seconds: number | null;
}

export interface Diagnostics {
  source_backend: string;
  source_database: boolean;
  semantic_index: boolean;
  background_jobs: boolean;
  background_job_counts: JobCounts;
  stale_running_jobs: number;
}
