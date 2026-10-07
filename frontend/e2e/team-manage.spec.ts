import { expect, signIn, test } from './fixtures';

/** A picture of one pixel, to upload. */
const PNG =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';

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

  test('money: the balance, the bank details and the payments', async ({ page }) => {
    await page.goto('/teams/rocket-team/money');

    await expect(page.getByRole('heading', { name: 'Money', level: 1 })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Bank details' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Export payments' })).toBeVisible();
  });

  test('the team page: a photo added, seen on the about page, and removed again', async ({
    page,
    isMobile,
  }) => {
    test.skip(isMobile, 'changes the sample data: once is enough');
    await page.goto('/teams/rocket-team/manage/page');
    const photos = page.getByRole('list', { name: 'Photos' }).getByRole('listitem');
    await expect(photos).toHaveCount(6);

    await page.locator('input[type="file"][multiple]').setInputFiles({
      name: 'new.png',
      mimeType: 'image/png',
      buffer: Buffer.from(PNG, 'base64'),
    });
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.getByText('Added.')).toBeVisible();
    await expect(photos).toHaveCount(7);

    await page.goto('/teams/rocket-team/about');
    await expect(page.getByRole('region', { name: 'Photos' }).getByRole('button')).toHaveCount(7);

    await page.goto('/teams/rocket-team/manage/page');
    const added = photos.last();
    await added.getByRole('button', { name: 'Remove' }).click();
    await added.getByRole('button', { name: 'Yes, remove' }).click();
    await expect(photos).toHaveCount(6);
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    for (const section of ['', '/members', '/former', '/page', '/applying', '/roles', '/money']) {
      const path = section === '/money' ? '/teams/rocket-team/money' : `/teams/rocket-team/manage${section}`;
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
  });
});

test.describe("a team's money for the association's treasurer", () => {
  test('every team, with the way to its transfers', async ({ page }) => {
    await signIn(page, 'treasurer@example.org');
    await page.goto('/teams/rocket-team/money');

    await expect(page.getByRole('heading', { name: 'Money', level: 1 })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Transfers (Admin)' })).toBeVisible();
  });
});
