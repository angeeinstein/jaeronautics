import { expect, signIn, test } from './fixtures';

test.describe("a team's management", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page, 'active@example.org');
  });

  test('the team sidebar, and a person from the members', async ({ page, isMobile }) => {
    await page.goto('/teams/rocket-team/manage');
    await expect(page.getByRole('heading', { name: 'Applications', level: 1 })).toBeVisible();

    if (isMobile) await page.getByRole('button', { name: 'Open the menu' }).click();
    const sections = page.getByRole('navigation', { name: 'Sections' }).last();
    await sections.getByRole('link', { name: 'Members', exact: true }).click();

    const members = page.getByRole('table', { name: 'Members' });
    await members.getByRole('link').first().click();
    await expect(page.getByRole('region', { name: 'Details' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Notes' })).toBeVisible();
  });

  test('an old link to a tab opens its page', async ({ page }) => {
    await page.goto('/teams/rocket-team/manage#manage-applying');

    await expect(page).toHaveURL(/\/teams\/rocket-team\/manage\/applying$/);
    await expect(page.getByRole('checkbox', { name: 'Accepting new members' })).toBeVisible();
  });

  test('roles: the leads, and the treasurer', async ({ page }) => {
    await page.goto('/teams/rocket-team/manage/roles');

    await expect(page.getByRole('region', { name: 'Leads' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Treasurer' })).toBeVisible();
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    for (const section of ['', '/members', '/former', '/page', '/applying', '/roles']) {
      const path = `/teams/rocket-team/manage${section}`;
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
  });
});
