import { expect, signIn, test } from './fixtures';

test.describe('an address that is no page', () => {
  test('is answered 404, and the app says so in its frame', async ({ page, watched }) => {
    watched.expected.push(/status of 404/);
    const response = await page.goto('/no-such-page');

    expect(response?.status()).toBe(404);
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
    await expect(page.getByRole('contentinfo')).toBeVisible();
    await expect(page.getByRole('link', { name: 'To the start page' })).toHaveAttribute('href', '/');
  });

  test('inside an area too', async ({ page }) => {
    await signIn(page);
    await page.goto('/admin');
    // An address the app's links could lead to, but that is no page of it.
    await page.evaluate(() => {
      window.history.pushState({}, '', '/admin/no-such-page');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
  });
});
