import http from "k6/http";
import { check } from "k6";
import exec from "k6/execution";
import { Counter } from "k6/metrics";

const baseUrl = __ENV.BASE_URL || "http://127.0.0.1:8000";
const apiKey = __ENV.API_KEY;
const acceptedJobs = new Counter("forge_jobs_accepted");
const waitEvery = Number(__ENV.WAIT_EVERY || 0);
const waitSeconds = Number(__ENV.WAIT_SECONDS || 2);

export const options = {
  scenarios: {
    submissions: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.RATE || 20),
      timeUnit: "1s",
      duration: __ENV.DURATION || "1m",
      preAllocatedVUs: Number(__ENV.PREALLOCATED_VUS || 20),
      maxVUs: Number(__ENV.MAX_VUS || 100),
    },
  },
  thresholds: {
    checks: ["rate>0.99"],
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500"],
    dropped_iterations: ["count==0"],
  },
};

export function setup() {
  if (!apiKey) {
    throw new Error("API_KEY must contain a Forge jobs:write credential");
  }
}

export default function () {
  const iteration = exec.scenario.iterationInTest;
  const unique = `${iteration}-${Date.now()}`;
  const waitJob = waitEvery > 0 && iteration % waitEvery === 0;
  const response = http.post(
    `${baseUrl}/v1/jobs`,
    JSON.stringify(
      waitJob
        ? {
            type: "demo.wait",
            payload: { task_id: `load-${unique}`, duration_seconds: waitSeconds },
            priority: "normal",
          }
        : {
            type: "demo.generate_report",
            payload: { report_id: `load-${unique}`, format: "json" },
            priority: "normal",
          },
    ),
    {
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        "Idempotency-Key": `load-${unique}`,
      },
      tags: { endpoint: "submit_job" },
    },
  );
  const accepted = check(response, {
    "submission accepted": (result) => result.status === 202,
  });
  if (accepted) {
    acceptedJobs.add(1);
  }
}

export function handleSummary(data) {
  const summaryPath = __ENV.SUMMARY_PATH || "load/results/k6-summary.json";
  const metric = (name) => data.metrics[name]?.values || {};
  const report = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    workload: {
      base_url: baseUrl,
      rate_per_second: Number(__ENV.RATE || 20),
      duration: __ENV.DURATION || "1m",
      preallocated_vus: Number(__ENV.PREALLOCATED_VUS || 20),
      max_vus: Number(__ENV.MAX_VUS || 100),
      wait_every: waitEvery,
      wait_seconds: waitSeconds,
    },
    metrics: {
      accepted_jobs: metric("forge_jobs_accepted"),
      checks: metric("checks"),
      dropped_iterations: metric("dropped_iterations"),
      http_request_duration: metric("http_req_duration"),
      http_request_failure_rate: metric("http_req_failed"),
      iterations: metric("iterations"),
    },
    thresholds: data.root_group?.checks || [],
  };
  return {
    [summaryPath]: `${JSON.stringify(report, null, 2)}\n`,
    stdout: `Forge k6 report written to ${summaryPath}\n`,
  };
}
