import { expect, signIn, test } from './fixtures';

test.describe('teams', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test('every team, each opening its page', async ({ page }) => {
    await page.goto('/admin/teams');

    const table = page.getByRole('table', { name: 'Teams' });
    await expect(table.getByRole('row', { name: /Glider Team/ })).toContainText('None');
    await table.getByRole('link', { name: 'Rocket Team' }).click();

    await expect(page).toHaveURL(/\/admin\/teams\/rocket-team$/);
    await expect(page.getByRole('heading', { name: 'Rocket Team', level: 1 })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue('Rocket Team');
    await expect(page.getByRole('table', { name: 'Roles' })).toContainText('Lead');
    await expect(page.getByRole('link', { name: 'Team page and settings' })).toHaveAttribute(
      'href',
      '/teams/rocket-team/manage',
    );
  });

  test('saving the settings unchanged changes nothing', async ({ page }) => {
    await page.goto('/admin/teams');

    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByText('Nothing changed.')).toBeVisible();
  });

  test('a fee change asks again before it is made', async ({ page }) => {
    await page.goto('/admin/teams/rocket-team');

    await page.getByRole('button', { name: 'Save fee' }).click();

    await expect(page.getByRole('button', { name: 'Yes, change the fee' })).toBeVisible();
  });

  test('a new team needs a name', async ({ page }) => {
    await page.goto('/admin/teams');
    await page.getByRole('link', { name: 'New Team' }).click();

    const create = page.getByRole('button', { name: 'Create' });
    await expect(create).toBeDisabled();
    await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Drone Team');
    await expect(create).toBeEnabled();
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    for (const path of ['/admin/teams', '/admin/teams/rocket-team', '/admin/teams/new']) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
  });
});
