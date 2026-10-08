import { expect, signIn, test } from './fixtures';

test.describe('settings', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test('a section from the sidebar, as a page of its own', async ({ page, isMobile }) => {
    await page.goto('/admin');
    if (isMobile) await page.getByRole('button', { name: 'Open the menu' }).click();
    const sections = page.getByRole('navigation', { name: 'Sections' }).last();

    await sections.getByRole('link', { name: 'Settings' }).click();
    await sections.getByRole('link', { name: 'General' }).click();

    await expect(page).toHaveURL(/\/admin\/settings\/general$/);
    await expect(page.getByRole('heading', { name: 'General', level: 1 })).toBeVisible();
    await expect(page.getByText('In use now: edu.fh-joanneum.at', { exact: true })).toBeVisible();
    await expect(page.getByText('In use now: fh-joanneum.at', { exact: true })).toBeVisible();
  });

  test('an old link to a section opens its page', async ({ page }) => {
    await page.goto('/admin/settings#settings-forum');

    await expect(page).toHaveURL(/\/admin\/settings\/forum$/);
    await expect(page.getByRole('heading', { name: 'Forum', level: 1 })).toBeVisible();
  });

  test('the forum: its addresses, and a test of the connection', async ({ page }) => {
    await page.goto('/admin/settings/forum');

    await expect(page.getByText('DiscourseConnect callback')).toBeVisible();
    await page.getByRole('button', { name: 'Test the connection' }).click();

    // The sample server has no forum to reach: said, not thrown.
    await expect(page.getByRole('status').filter({ hasText: /./ })).toBeVisible();
  });

  test('the membership fee shows of a secret only whether one is set', async ({ page }) => {
    await page.goto('/admin/settings/billing');

    await expect(page.getByLabel('Secret key')).toHaveValue('');
    await expect(page.getByText(/None set yet\.|One is set\./).first()).toBeVisible();
  });

  test('a mail account: added, listed without its password, removed', async ({ page }) => {
    await page.goto('/admin/settings/mail');
    const form = page.getByRole('region', { name: 'Add an account' });
    await form.getByRole('textbox', { name: 'Key', exact: true }).fill('e2e-office');
    await form.getByRole('textbox', { name: 'SMTP server' }).fill('smtp.example.org');
    await form.getByRole('textbox', { name: 'Username' }).fill('office@example.org');
    await form.getByLabel('Password', { exact: true }).fill('not-shown');
    await form.getByRole('button', { name: 'Add' }).click();

    const row = page.getByRole('table', { name: 'Mail accounts' }).getByRole('row', { name: /e2e-office/ });
    await expect(row).toContainText('smtp.example.org');
    await expect(page.getByText('not-shown')).toHaveCount(0);

    await row.getByRole('button', { name: 'Remove' }).click();
    await page.getByRole('button', { name: 'Yes, remove' }).click();
    await expect(row).toHaveCount(0);
  });

  test('system health: the report, and the figures behind it', async ({ page }) => {
    await page.goto('/admin/settings/health');

    const report = page.getByRole('region', { name: 'Report' });
    await expect(report.getByText(/^(Healthy|Needs attention)$/)).toBeVisible();
    await expect(page.getByRole('region', { name: 'Membership' })).toContainText('Members');
    await expect(page.getByRole('region', { name: 'Background work' })).toContainText('Emails queued');
  });

  test('updates: the last one, its steps ticked off, and its whole output to unfold', async ({ page }) => {
    await page.goto('/admin/settings/updates');

    await expect(page.getByRole('region', { name: 'Version', exact: true })).toContainText('Installed');
    const steps = page.getByRole('list', { name: 'Steps of the last update' });
    await expect(steps.getByRole('listitem')).toHaveCount(12);
    await expect(steps).toContainText('Fetching the front end built by CI: Done');
    await expect(steps).toContainText('CI is still running for this revision');

    await page.getByRole('button', { name: 'Show the terminal output' }).click();
    await expect(page.getByText(/Running upgrade a7d3e9f1c5b2 -> b8e4f2a6c1d9/)).toBeVisible();
  });

  test('backup and restore: the steps of a backup, made in the background', async ({ page }) => {
    await page.goto('/admin/settings/backup');

    await expect(page.getByRole('region', { name: 'Restore' })).toContainText('install.sh --restore');
    await page.getByLabel('Passphrase', { exact: true }).fill('a long enough passphrase');
    await page.getByLabel('Passphrase again').fill('a long enough passphrase');
    await page.getByRole('button', { name: 'Back up' }).click();

    await expect(page.getByRole('list', { name: 'Making the backup' })).toBeVisible();
  });

  test('an old link to backups opens their page', async ({ page }) => {
    await page.goto('/admin/settings#backup-restore');

    await expect(page).toHaveURL(/\/admin\/settings\/backup$/);
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    for (const path of [
      '/admin/settings/general',
      '/admin/settings/notifications',
      '/admin/settings/forum',
      '/admin/settings/mail',
      '/admin/settings/test-email',
      '/admin/settings/health',
      '/admin/settings/updates',
      '/admin/settings/backup',
    ]) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
  });
});
