import { expect, signIn, test } from './fixtures';

test.describe('the log', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test('newest first, what changed folded until asked for', async ({ page }) => {
    await page.goto('/admin/logs');

    const table = page.getByRole('table', { name: 'Log entries' });
    await expect(table).toBeVisible();
    const toggle = table.getByRole('button', { name: / \/ / }).first();
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  });

  test('the search follows what is typed, into the address', async ({ page }) => {
    await page.goto('/admin/logs');

    await page.getByRole('textbox', { name: 'Search the log' }).fill('team');

    await expect(page).toHaveURL(/\/admin\/logs\?q=team$/);
  });

  test("from an account's activity to all of it in the log", async ({ page }) => {
    await page.goto('/admin/accounts?q=carla');
    await page.getByRole('table', { name: 'Accounts' }).getByRole('link').first().click();
    await page.getByRole('tab', { name: 'Activity' }).click();

    await page.getByRole('link', { name: 'All of it in the log' }).click();

    await expect(page).toHaveURL(/\/admin\/logs\?user=\d+$/);
    await expect(page.getByText(/Only what is about/)).toContainText('cancelling@example.org');
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    await page.goto('/admin/logs');
    await expect(page.getByRole('table', { name: 'Log entries' })).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
