import { expect, signIn, test } from './fixtures';

// The example portal (scripts/visual/snapshot.py) has credit on: Anna topped
// up €10.00, handed over €5.00 in cash and had a coffee (€1.20) from the
// association; Carla bought a beer (€2.00) from the Rocket Team.

test.describe('credit', () => {
  test('a member: the tile, the balance and the history', async ({ page, isMobile }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/account');

    // The tile, not the side menu's entry of the same name.
    const tile = page.getByRole('link', { name: 'Credit' }).filter({ hasText: '€' });
    await expect(tile).toContainText('€13.80');
    await page.goto('/account/credit');

    await expect(page.getByRole('heading', { level: 1, name: 'Credit' })).toBeVisible();
    const history = page.getByRole('table', { name: 'History' });
    await expect(history.getByRole('row', { name: /Coffee/ })).toContainText('−€1.20');
    await expect(history.getByRole('row', { name: /Handed over at the office/ })).toContainText('+€5.00');
    // €13.80 of at most €20.00: less room than the smallest top-up.
    await expect(page.getByText('You can top up again once your credit is below €10.00.')).toBeVisible();
    if (isMobile) {
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
      // The amount stays in view; the balance after each entry is left out.
      await expect(history.getByRole('row', { name: /Coffee/ }).getByText('−€1.20')).toBeInViewport();
    }
  });

  test('the treasurer: every balance, and one person to book for', async ({ page }) => {
    await signIn(page, 'treasurer@example.org');
    await page.goto('/admin/credit');

    await expect(page.getByText('Held for members')).toBeVisible();
    const balances = page.getByRole('table', { name: 'Balances' });
    await expect(balances.getByRole('row')).toHaveCount(3);
    await balances.getByRole('link', { name: /^Anna/ }).click();

    await expect(page).toHaveURL(/\/admin\/credit\/\d+$/);
    await expect(page.getByRole('button', { name: 'Book' })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Refund €/ })).toBeVisible();
  });

  test('a lead keeps the team’s prices, and its money shows what it sold', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/teams/rocket-team/manage/prices');

    const list = page.getByRole('table', { name: 'Price list' });
    await expect(list.getByRole('row', { name: /Beer/ })).toContainText('€2.00');
    await page.goto('/teams/rocket-team/money');
    await expect(
      page.getByRole('table', { name: 'Sold for credit' }).getByRole('row', { name: /Beer/ }),
    ).toContainText('€2.00');
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    await signIn(page);
    for (const path of ['/admin/credit', '/admin/settings/credit', '/admin/money']) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
  });
});
