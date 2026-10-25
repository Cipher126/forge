import { expect, test } from '@playwright/test';

test.use({ trace: 'off', screenshot: 'off', video: 'off' });
test('real stack: connect, submit, execute, inspect persisted result and workers', async ({
  page,
}) => {
  test.skip(
    !process.env.FORGE_E2E_KEY,
    'Run with scripts/validate_dashboard.py for an expiring credential.',
  );
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await page.getByLabel('API key', { exact: true }).fill(process.env.FORGE_E2E_KEY!);
  await page.getByRole('button', { name: 'Connect to Forge' }).click();
  await expect(page.getByRole('heading', { name: 'The control room.' })).toBeVisible();
  await expect(page.getByText('Live', { exact: true })).toBeVisible({ timeout: 30000 });
  await page.getByRole('link', { name: 'Submit job', exact: true }).click();
  const reportId = `browser-${crypto.randomUUID()}`;
  await page
    .getByLabel('Payload (JSON)')
    .fill(JSON.stringify({ report_id: reportId, format: 'json' }));
  await page.getByRole('button', { name: 'Submit job', exact: true }).click();
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/);
  await expect(page.getByRole('heading', { name: 'Transition timeline' })).toBeVisible();
  await expect(page.locator('.attempt-list').getByText('succeeded', { exact: true })).toBeVisible({
    timeout: 60000,
  });
  await expect(
    page.locator('.json-view').filter({ hasText: `reports/${reportId}.json` }),
  ).toBeVisible({ timeout: 30000 });
  await page.getByRole('link', { name: 'Workers & queues', exact: true }).click();
  await expect(page.locator('.worker-card').first()).toBeVisible();
  await page.getByRole('link', { name: 'Overview', exact: true }).click();
  await page.getByRole('link', { name: 'Submit job', exact: true }).click();
  await page.getByLabel(/Schedule for/).fill('2099-01-01T12:00');
  await page.getByRole('button', { name: 'Submit job', exact: true }).click();
  await expect(page.locator('.page-heading').getByText('scheduled', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel job', exact: true }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Confirm', exact: true }).click();
  await expect(page.locator('.page-heading').getByText('cancelled', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Overview', exact: true }).click();
  await page.screenshot({
    path: '../tests/reports/dashboard-live-desktop.png',
    fullPage: true,
    animations: 'disabled',
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: '../tests/reports/dashboard-live-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  expect(errors).toEqual([]);
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Connect to Forge' })).toBeVisible();
});
