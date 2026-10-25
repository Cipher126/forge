import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const id = '019abcde-1234-7000-8000-123456789012';
const now = new Date().toISOString();
const job = {
  id,
  replayed_from_job_id: null,
  type: 'demo.generate_report',
  handler_version: '1',
  status: 'succeeded',
  priority: 'normal',
  payload: { report_id: 'test-report', format: 'json' },
  result: { report_id: 'test-report', format: 'json', artifact: 'reports/test-report.json' },
  max_attempts: 3,
  attempt_count: 1,
  created_at: now,
  updated_at: now,
  queued_at: now,
  started_at: now,
  finished_at: now,
  run_at: null,
  payload_stored_externally: false,
  result_stored_externally: false,
};
const identity = {
  id: 'owner-id',
  public_id: 'public-id',
  name: 'Test operator',
  scopes: ['admin'],
  enabled: true,
  rate_limit_requests: 1000,
  rate_limit_window_s: 60,
  created_at: now,
  expires_at: null,
  last_used_at: now,
  revoked_at: null,
};
const distribution = { sample_count: 10, p50_seconds: 0.1, p95_seconds: 0.2 };

async function mockApi(page: Page, scopes = ['admin']) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace('/api', '');
    let data: unknown = {};
    if (path === '/v1/auth/me') data = { ...identity, scopes };
    else if (path === '/ready')
      data = { status: 'ready', postgres: true, redis: true, object_store: true };
    else if (path.endsWith('/summary'))
      data = {
        generated_at: now,
        jobs_by_status: { succeeded: 10 },
        outstanding_jobs: 0,
        terminal_jobs: 10,
        active_workers: 2,
        queues: {
          queued_total: 0,
          queued_by_priority: { high: 0, normal: 0, low: 0 },
          processing_by_worker: {},
          processing_total: 0,
          durable_outstanding: 0,
        },
      };
    else if (path.endsWith('/performance'))
      data = {
        generated_at: now,
        window_minutes: 60,
        submitted_jobs: 10,
        completed_jobs: 10,
        success_rate: 1,
        throughput_per_minute: 0.16,
        queue_wait: distribution,
        execution_time: distribution,
        end_to_end_time: distribution,
        scheduling_lag: distribution,
        claim_time: distribution,
      };
    else if (path.endsWith('/events'))
      return route.fulfill({ status: 503, contentType: 'application/json', body: '{}' });
    else if (path.endsWith('/handlers'))
      data = {
        items: [
          {
            job_type: 'demo.generate_report',
            version: '1',
            active: true,
            execution_mode: 'async',
            timeout_seconds: 30,
            default_max_attempts: 3,
            payload_schema: { type: 'object' },
          },
          { job_type: 'demo.wait', version: '1', active: true, payload_schema: {} },
        ],
      };
    else if (path === '/v1/jobs' && route.request().method() === 'POST')
      data = { ...job, ...route.request().postDataJSON(), status: 'queued', result: null };
    else if (path === '/v1/jobs')
      data = {
        items:
          url.searchParams.get('status') === 'dead_lettered'
            ? [{ ...job, status: 'dead_lettered' }]
            : [job],
        next_cursor: null,
      };
    else if (path === `/v1/jobs/${id}`) data = job;
    else if (path.endsWith('/attempts'))
      data = {
        items: [
          {
            id: 'attempt-1',
            attempt_number: 1,
            status: 'succeeded',
            worker_id: 'worker-1',
            started_at: now,
            finished_at: now,
            lease_expires_at: now,
            error_code: null,
          },
        ],
      };
    else if (path.endsWith('/transitions'))
      data = {
        items: [
          {
            id: 1,
            job_id: id,
            to_status: 'succeeded',
            reason: 'attempt_succeeded',
            actor_type: 'worker',
            created_at: now,
          },
        ],
      };
    else if (path.endsWith('/result')) data = { result: job.result, stored_externally: false };
    else if (path.endsWith('/api-keys') && route.request().method() === 'POST')
      data = {
        id: 'new-key',
        name: 'Service test',
        scopes: ['jobs:read'],
        api_key: 'forge_live_one_time_test_value',
        expires_at: null,
      };
    else if (path.endsWith('/api-keys')) data = { items: [identity], next_cursor: null };
    else if (path.endsWith('/workers'))
      data = {
        items: [
          {
            worker_id: 'worker-1',
            status: 'active',
            heartbeat_ttl_seconds: 12,
            active_attempts: 0,
            reserved_messages: 0,
          },
        ],
      };
    else if (path.endsWith('/queues'))
      data = {
        queued_by_priority: { high: 0, normal: 0, low: 0 },
        queued_total: 0,
        durable_outstanding: 0,
        processing_total: 0,
        processing_by_worker: {},
      };
    else if (path.endsWith('/audit-events')) data = { items: [], next_cursor: null };
    return route.fulfill({
      status: route.request().method() === 'POST' ? 201 : 200,
      contentType: 'application/json',
      body: JSON.stringify(data),
    });
  });
}

async function connect(page: Page) {
  await page.goto('/');
  await page.getByLabel('API key', { exact: true }).fill('test-session-key');
  await page.getByRole('button', { name: 'Connect to Forge' }).click();
}

test.describe('dashboard contract and UI', () => {
  test('connects, displays metrics and clears the credential on reload', async ({ page }) => {
    await mockApi(page);
    await connect(page);
    await expect(page.getByRole('heading', { name: 'The control room.' })).toBeVisible();
    expect(
      await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })),
    ).not.toContain('test-session-key');
    await page.reload();
    await expect(page.getByRole('button', { name: 'Connect to Forge' })).toBeVisible();
  });
  test('submits work with an idempotency key and follows attempts and result', async ({ page }) => {
    await mockApi(page);
    await connect(page);
    await page.getByRole('link', { name: 'Submit job', exact: true }).click();
    const submission = page.waitForRequest(
      (req) => req.url().endsWith('/api/v1/jobs') && req.method() === 'POST',
    );
    await page.getByRole('button', { name: 'Submit job', exact: true }).click();
    const sent = await submission;
    expect(sent.headers()['idempotency-key']).toBeTruthy();
    expect(sent.headers().authorization).toBe('Bearer test-session-key');
    await expect(page.getByRole('heading', { name: 'Transition timeline' })).toBeVisible();
    await expect(page.getByText('test-report', { exact: false }).first()).toBeVisible();
    await expect(page.getByText('Attempt 1', { exact: true })).toBeVisible();
  });
  test('filters jobs using server query parameters', async ({ page }) => {
    await mockApi(page);
    await connect(page);
    await page.getByRole('link', { name: 'Jobs', exact: true }).click();
    await page.getByLabel('Filter status').selectOption('dead_lettered');
    await expect(page.getByRole('table').getByText('dead lettered', { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/status=dead_lettered/);
  });
  test('hides admin controls for a scoped reader and blocks direct admin navigation', async ({
    page,
  }) => {
    await mockApi(page, ['jobs:read']);
    await connect(page);
    await expect(page.getByRole('link', { name: 'API keys', exact: true })).not.toBeVisible();
    await page.goto('/keys');
    await page.getByLabel('API key', { exact: true }).fill('test-session-key');
    await page.getByRole('button', { name: 'Connect to Forge' }).click();
    await expect(page.getByText('This view requires a different scope')).toBeVisible();
  });
  test('creates a key, reveals it once, then discards the displayed secret', async ({ page }) => {
    await mockApi(page);
    await connect(page);
    await page.getByRole('link', { name: 'API keys', exact: true }).click();
    await page.getByRole('button', { name: 'Create key', exact: true }).click();
    await page.getByLabel('Name', { exact: true }).fill('Service test');
    await page.getByRole('dialog').getByRole('button', { name: 'Create key', exact: true }).click();
    await expect(page.getByTestId('new-api-key')).toHaveText('forge_live_one_time_test_value');
    await page.getByRole('button', { name: 'I saved it' }).click();
    await expect(page.getByTestId('new-api-key')).not.toBeVisible();
  });
  test('shows a sanitized connection error without entering the dashboard', async ({ page }) => {
    await mockApi(page);
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({
          error: { code: 'invalid_api_key', message: 'Invalid or expired API key' },
        }),
      }),
    );
    await connect(page);
    await expect(page.getByRole('alert')).toContainText('Invalid or expired API key');
  });
  test('is usable on a narrow screen without overflowing the page', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockApi(page);
    await connect(page);
    await expect(page.getByRole('heading', { name: 'The control room.' })).toBeVisible();
    await page.getByRole('button', { name: 'Open navigation' }).click();
    await page.getByRole('link', { name: 'Jobs', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Every job has a story.' })).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
      .toBe(true);
  });
  test('cancels a running job only after confirmation', async ({ page }) => {
    await mockApi(page);
    let cancelled = false;
    await page.route(`**/api/v1/jobs/${id}`, (route) =>
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ ...job, status: cancelled ? 'cancellation_requested' : 'running' }),
      }),
    );
    await page.route(`**/api/v1/jobs/${id}/cancel`, (route) => {
      cancelled = true;
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ ...job, status: 'cancellation_requested' }),
      });
    });
    await connect(page);
    await page.getByRole('link', { name: 'Jobs', exact: true }).click();
    await page.getByRole('link', { name: `Open job ${id}`, exact: true }).click();
    await page.getByRole('button', { name: 'Cancel job', exact: true }).click();
    expect(cancelled).toBe(false);
    await expect(page.getByRole('alertdialog')).toContainText('cooperative');
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Confirm', exact: true })
      .click();
    await expect(
      page.locator('.page-heading').getByText('cancellation requested', { exact: true }),
    ).toBeVisible();
    expect(cancelled).toBe(true);
  });
  test('replay retries keep one idempotency key and navigate to the new linked job', async ({
    page,
  }) => {
    await mockApi(page);
    const replayId = '019abcde-1234-7000-8000-123456789013';
    const sentKeys: string[] = [];
    await page.route(`**/api/v1/jobs/${id}`, (route) =>
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ ...job, status: 'dead_lettered' }),
      }),
    );
    await page.route(`**/api/v1/jobs/${replayId}`, (route) =>
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ ...job, id: replayId, replayed_from_job_id: id }),
      }),
    );
    await page.route(`**/api/v1/jobs/${id}/replay`, (route) => {
      sentKeys.push(route.request().headers()['idempotency-key']);
      return route.fulfill({
        status: sentKeys.length === 1 ? 503 : 202,
        contentType: 'application/json',
        body: JSON.stringify(
          sentKeys.length === 1
            ? { error: { code: 'dependency_unavailable', message: 'Temporarily unavailable' } }
            : { ...job, id: replayId, replayed_from_job_id: id },
        ),
      });
    });
    await connect(page);
    await page.getByRole('link', { name: 'Jobs', exact: true }).click();
    await page.getByRole('link', { name: `Open job ${id}`, exact: true }).click();
    await page.getByRole('button', { name: 'Replay job', exact: true }).click();
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Confirm', exact: true })
      .click();
    await expect(page.getByRole('alertdialog').getByRole('alert')).toContainText(
      'Temporarily unavailable',
    );
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Confirm', exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(replayId));
    expect(sentKeys.length).toBe(2);
    expect(sentKeys[0]).toBeTruthy();
    expect(sentKeys[0]).toBe(sentKeys[1]);
  });
});
