import { api } from "./api";
import type { ServiceJob } from "./types";

const TERMINAL_STATUSES = new Set<ServiceJob["status"]>([
  "succeeded",
  "failed",
  "canceled",
  "interrupted",
]);

export function isTerminalJob(job: ServiceJob): boolean {
  return TERMINAL_STATUSES.has(job.status);
}

export async function waitForJob(
  initial: ServiceJob,
  options: {
    signal?: AbortSignal;
    onUpdate?: (job: ServiceJob) => void;
    intervalMs?: number;
  } = {},
): Promise<ServiceJob> {
  let current = initial;
  options.onUpdate?.(current);
  while (!isTerminalJob(current)) {
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, options.intervalMs ?? 250);
      options.signal?.addEventListener("abort", () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      }, { once: true });
    });
    current = await api.job(initial.repository_id, initial.job_id);
    options.onUpdate?.(current);
  }
  if (current.status !== "succeeded") {
    const message = current.error_message || (
      current.status === "canceled" ? "任务已取消。" : "后台任务没有完成。"
    );
    throw new Error(message);
  }
  return current;
}
