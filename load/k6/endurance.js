import http from "k6/http";
import { sleep } from "k6";
import exec from "k6/execution";
import { Counter, Trend } from "k6/metrics";

const baseUrl = __ENV.BASE_URL || "http://127.0.0.1:8000";
const apiKey = __ENV.API_KEY;
const jobs = Number(__ENV.JOBS || 100000);
const accepted = new Counter("forge_jobs_accepted");
const failed = new Counter("forge_jobs_exhausted");
const retried = new Counter("forge_submission_retries");
const serverErrors = new Counter("forge_server_errors");
const submission = new Trend("forge_submission_total_ms", true);
const runId = __ENV.RUN_ID || Date.now().toString();
const vus = Number(__ENV.VUS || 8);

export const options = {
  scenarios: {
    endurance: {
      executor: "shared-iterations",
      vus,
      iterations: jobs,
      maxDuration: __ENV.MAX_DURATION || "90m",
    },
  },
  thresholds: {
    forge_jobs_accepted: [`count==${jobs}`],
    forge_jobs_exhausted: ["count==0"],
    forge_server_errors: ["count==0"],
  },
};

export function setup() {
  if (!apiKey) throw new Error("API_KEY is required");
}

export default function () {
  const id = `endurance-${runId}-${exec.scenario.iterationInTest}`;
  const body = JSON.stringify({
    type: "demo.generate_report",
    payload: { report_id: id, format: "json" },
    priority: ["high", "normal", "low"][exec.scenario.iterationInTest % 3],
  });
  const started = Date.now();
  let success = false;
  for (let attempt = 0; attempt < 120; attempt++) {
    const response = http.post(`${baseUrl}/v1/jobs`, body, {
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        "Idempotency-Key": id,
      },
      timeout: "10s",
      responseType: "none",
    });
    serverErrors.add(response.status >= 500 && response.status !== 503 ? 1 : 0);
    if (response.status === 202 || response.status === 200) {
      success = true;
      break;
    }
    // A transport failure may follow a successful commit; retry the identical key and body.
    if (![0, 429, 503].includes(response.status)) break;
    retried.add(1);
    const after = Math.min(5, Math.max(0.1, Number(response.headers["Retry-After"] || 0.1)));
    sleep(after + Math.random() * 0.1);
  }
  accepted.add(success ? 1 : 0);
  failed.add(success ? 0 : 1);
  submission.add(Date.now() - started);
}

export function handleSummary(data) {
  const metric = (name) => data.metrics[name]?.values || {};
  const report = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    workload: { jobs, vus, base_url: baseUrl, priority_mix: "equal high/normal/low" },
    metrics: {
      accepted_jobs: metric("forge_jobs_accepted"),
      exhausted_jobs: metric("forge_jobs_exhausted"),
      submission_retries: metric("forge_submission_retries"),
      server_errors: metric("forge_server_errors"),
      submission_total_duration: metric("forge_submission_total_ms"),
      http_request_duration: metric("http_req_duration"),
      iterations: metric("iterations"),
    },
  };
  const path = __ENV.SUMMARY_PATH || "/load/results/endurance-k6.json";
  return { [path]: JSON.stringify(report, null, 2) + "\n", stdout: "Endurance summary saved.\n" };
}
