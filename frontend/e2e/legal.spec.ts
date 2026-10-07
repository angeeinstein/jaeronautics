import { expect, test } from './fixtures';

test.describe('the legal texts, for anybody', () => {
  test('the list, a text, and the way between them', async ({ page }) => {
    await page.goto('/legal');
    await expect(page.getByRole('heading', { name: 'Legal texts', level: 1 })).toBeVisible();

    await page.getByRole('link', { name: 'Statutes', exact: true }).first().click();

    await expect(page).toHaveURL(/\/legal\/statutes$/);
    await expect(page.getByRole('link', { name: 'Legal texts' }).first()).toBeVisible();
    await expect(page.locator('h1')).toBeVisible();
  });

  test('the footer leads there from every page', async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('contentinfo').getByRole('link', { name: 'Legal texts' }).click();

    await expect(page).toHaveURL(/\/legal$/);
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    for (const path of ['/legal', '/legal/statutes', '/legal/statutes/de']) {
      await page.goto(path);
      await expect(page.locator('h1')).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
  });
});
