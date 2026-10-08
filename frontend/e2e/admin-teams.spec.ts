import { expect, signIn, test } from './fixtures';

test.describe('teams', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("every team, each opening the team's own pages with the admins' settings", async ({ page }) => {
    await page.goto('/admin/teams');

    const table = page.getByRole('table', { name: 'Teams' });
    await expect(table.getByRole('row', { name: /Glider Team/ })).toContainText('None');
    await table.getByRole('link', { name: 'Rocket Team' }).click();

    await expect(page).toHaveURL(/\/teams\/rocket-team\/manage\/details$/);
    await expect(page.getByRole('heading', { name: 'Details', level: 1 })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue('Rocket Team');
  });

  test('the old address of a team goes to its own pages', async ({ page }) => {
    await page.goto('/admin/teams/rocket-team');

    await expect(page).toHaveURL(/\/teams\/rocket-team\/manage\/details$/);
  });

  test('saving the settings unchanged changes nothing', async ({ page }) => {
    await page.goto('/admin/teams');

    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByText('Nothing changed.')).toBeVisible();
  });

  test('a fee change asks again before it is made', async ({ page }) => {
    await page.goto('/teams/rocket-team/manage/fee');

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
    for (const path of [
      '/admin/teams',
      '/admin/teams/new',
      '/teams/rocket-team/manage/details',
      '/teams/rocket-team/manage/fee',
      '/teams/rocket-team/manage/archive',
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
