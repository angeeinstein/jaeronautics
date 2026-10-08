import { expect, signIn, test } from './fixtures';

test.describe('one account', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test('opens from the list, in the app, with its state', async ({ page }) => {
    await page.goto('/admin/accounts?q=carla');
    await page.getByRole('table', { name: 'Accounts' }).getByRole('link', { name: 'Carla Cancel' }).click();

    await expect(page.getByRole('heading', { name: 'Carla Cancel', level: 1 })).toBeVisible();
    await expect(
      page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Accounts' }),
    ).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Profile', selected: true })).toBeVisible();
  });

  test('a tab is in the address, and stays on reload', async ({ page }) => {
    await page.goto('/admin/accounts?q=carla');
    await page.getByRole('table', { name: 'Accounts' }).getByRole('link', { name: 'Carla Cancel' }).click();

    await page.getByRole('tab', { name: 'Membership' }).click();
    await expect(page).toHaveURL(/#membership$/);
    await expect(page.getByText('No, it ends with the paid period')).toBeVisible();
    await page.reload();

    await expect(page.getByRole('tab', { name: 'Membership', selected: true })).toBeVisible();
  });

  test('deactivating and reactivating', async ({ page }) => {
    await page.goto('/admin/accounts?q=rejected');
    await page.getByRole('table', { name: 'Accounts' }).getByRole('link', { name: 'Rene Rejected' }).click();
    await page.getByRole('tab', { name: 'Danger zone' }).click();

    await page.getByLabel('Reason', { exact: true }).fill('Testing the switch');
    await page.getByRole('button', { name: 'Deactivate', exact: true }).click();
    await page.getByRole('button', { name: 'Yes, deactivate' }).click();
    await expect(page.getByText('The account is deactivated. The membership is unchanged.')).toBeVisible();
    await expect(page.getByText('Cannot sign in or use the forum: Testing the switch')).toBeVisible();

    await page.getByRole('button', { name: 'Reactivate' }).click();
    await expect(page.getByRole('button', { name: 'Deactivate', exact: true })).toBeVisible();
  });

  test('erasing asks for the address first', async ({ page }) => {
    await page.goto('/admin/accounts?q=carla');
    await page.getByRole('table', { name: 'Accounts' }).getByRole('link', { name: 'Carla Cancel' }).click();
    await page.getByRole('tab', { name: 'Danger zone' }).click();

    const erase = page.getByRole('button', { name: 'Erase personal data' });
    await expect(erase).toBeDisabled();
    await page.getByLabel('Type cancelling@example.org to confirm').fill('someone@else.org');
    await expect(erase).toBeDisabled();
  });

  test('one’s own roles are not changed here', async ({ page }) => {
    await page.goto('/admin/accounts?q=admin%40example.org');
    await page
      .getByRole('table', { name: 'Accounts' })
      .getByRole('link', { name: 'admin@example.org' })
      .click();
    await page.getByRole('tab', { name: 'Roles' }).click();

    await expect(page.getByText(/cannot change your own roles/)).toBeVisible();
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    await page.goto('/admin/accounts?q=carla');
    await page.getByRole('table', { name: 'Accounts' }).getByRole('link', { name: 'Carla Cancel' }).click();
    await expect(page.getByRole('tablist')).toBeVisible();

    for (const tab of ['Profile', 'Membership', 'Forum', 'Roles', 'Danger zone']) {
      await page.getByRole('tab', { name: tab }).click();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, tab).toBeLessThanOrEqual(0);
    }
  });
});
