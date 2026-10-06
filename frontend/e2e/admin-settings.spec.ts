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
    await expect(page.getByText(/^In use now: /)).toBeVisible();
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

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    for (const path of [
      '/admin/settings/general',
      '/admin/settings/notifications',
      '/admin/settings/forum',
      '/admin/settings/mail',
      '/admin/settings/test-email',
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
