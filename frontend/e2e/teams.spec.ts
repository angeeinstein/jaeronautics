import { expect, signIn, test } from './fixtures';

test.describe('teams, for members', () => {
  test('the overview: a member sees their team first and opens it', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/teams');

    const mine = page.getByRole('region', { name: 'My teams' });
    await expect(mine.getByText('Member', { exact: true }).first()).toBeVisible();
    await mine.getByRole('link', { name: 'Open' }).first().click();

    await expect(page).toHaveURL(/\/teams\/rocket-team$/);
    await expect(page.getByRole('list', { name: 'Members' })).toBeVisible();
  });

  test('somebody not in it reads what it is about, with the form to apply', async ({ page }) => {
    await signIn(page, 'photo-needed@example.org');
    await page.goto('/teams/rocket-team');

    await expect(page).toHaveURL(/\/teams\/rocket-team\/about$/);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await expect(page.locator('#join')).toBeVisible();
  });

  test('the about page presents the team: its story, and photos to look through', async ({ page }) => {
    await signIn(page, 'photo-needed@example.org');
    await page.goto('/teams/rocket-team/about');

    const cover = page.getByRole('region', { name: 'Rocket Team' });
    await expect(cover.getByText('Applications open')).toBeVisible();
    await expect(page.getByRole('heading', { level: 2, name: 'What members do' })).toBeVisible();
    const photos = page.getByRole('region', { name: 'Photos' }).getByRole('button');
    await expect(photos).toHaveCount(6);

    await photos.first().click();
    const viewer = page.getByRole('dialog');
    await expect(viewer).toContainText('1 / 6');
    await expect(
      viewer.getByRole('img', { name: 'Launch day at the European Rocketry Challenge' }),
    ).toBeVisible();
    await page.keyboard.press('ArrowRight');
    await expect(viewer).toContainText('Integration in the workshop');
    await page.keyboard.press('Escape');
    await expect(viewer).toBeHidden();
  });

  test('leaving is a page of its own, and asks for a deliberate yes', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/teams/rocket-team/leave');

    await expect(page.getByRole('button', { name: 'Leave the team' })).toBeDisabled();
    await expect(page.getByText('To come back later, you apply again and the leads decide.')).toBeVisible();
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    await signIn(page, 'active@example.org');
    for (const path of [
      '/teams',
      '/teams/rocket-team',
      '/teams/rocket-team/about',
      '/teams/rocket-team/leave',
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
