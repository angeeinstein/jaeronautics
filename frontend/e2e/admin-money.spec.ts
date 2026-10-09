import { expect, signIn, test } from './fixtures';

test.describe('money', () => {
  test('what each team is owed, and a transfer to one', async ({ page }) => {
    await signIn(page);
    await page.goto('/admin/money');

    const table = page.getByRole('table', { name: 'What the teams are owed' });
    await expect(table.getByRole('row', { name: /Rocket Team/ })).toContainText('€12.00');
    await table.getByRole('link', { name: 'Rocket Team' }).click();

    await expect(page).toHaveURL(/\/admin\/money\/rocket-team$/);
    const code = page.getByRole('img', { name: 'Transfer code for €12.00' });
    await expect(code).toBeVisible();
    expect(await code.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
    await page.getByRole('button', { name: 'Mark as transferred' }).click();
    await expect(page.getByRole('button', { name: 'Yes, record €12.00' })).toBeVisible();
  });

  test('the treasurer sees the money and nothing else', async ({ page, isMobile }) => {
    await signIn(page, 'treasurer@example.org');
    await page.goto('/admin');

    await expect(page.getByText('What needs doing.')).toBeVisible();
    const attention = page.getByRole('region', { name: 'Needs your attention' });
    await expect(attention).toContainText('€12.00 open');
    await expect(page.getByText('Active members')).toHaveCount(0);
    if (!isMobile) {
      // On a phone the sections are behind the menu button.
      const sidebar = page.getByRole('navigation', { name: 'Sections' });
      await expect(sidebar.getByRole('link', { name: 'Money' })).toBeVisible();
      await expect(sidebar.getByRole('link', { name: 'Accounts' })).toHaveCount(0);
    }
    await attention.getByRole('link', { name: 'Open Money' }).click();

    await expect(page).toHaveURL(/\/admin\/money$/);
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    await signIn(page);
    for (const path of ['/admin/money', '/admin/money/rocket-team']) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
  });
});
