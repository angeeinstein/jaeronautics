import { expect, signIn, test } from './fixtures';

test('the dashboard: what is waiting, the figures, the latest log entries', async ({ page }) => {
  await signIn(page);
  await page.goto('/admin');

  const attention = page.getByRole('region', { name: 'Needs your attention' });
  await expect(attention.getByRole('link', { name: 'Review' }).first()).toHaveAttribute(
    'href',
    '/admin/reviews#review-queue',
  );
  await expect(page.getByRole('link', { name: /Active members/ })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Recent activity' })).toBeVisible();

  // A figure leads to its list -- still one of Flask's pages.
  await page.getByRole('link', { name: /Active members/ }).click();
  await expect(page).toHaveURL(/\/admin\/accounts\?active=active$/);
});
