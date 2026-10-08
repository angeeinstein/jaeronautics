import { expect, signIn, test } from './fixtures';

test.describe('reviews', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test('from the dashboard to what is waiting', async ({ page }) => {
    await page.goto('/admin');

    await page
      .getByRole('region', { name: 'Needs your attention' })
      .getByRole('link', { name: 'Review' })
      .first()
      .click();

    await expect(page).toHaveURL(/\/admin\/reviews#review-queue$/);
    await expect(page.getByRole('heading', { name: 'Reviews', level: 1 })).toBeVisible();
  });

  test('a picture and a change request, each with its decision', async ({ page }) => {
    await page.goto('/admin/reviews');

    const picture = page.getByRole('region', { name: 'Profile picture: Petra Pending' });
    await expect(picture.getByRole('img', { name: /Petra Pending/ })).toBeVisible();
    await expect(picture.getByRole('button', { name: 'Approve and send to forum' })).toBeVisible();

    const change = page.getByRole('region', { name: /Name change: Paul Photo/ });
    await expect(change.getByRole('table', { name: 'What changes' })).toContainText('Photograph');
    await change.getByRole('button', { name: 'Reject' }).click();
    await expect(change.getByRole('button', { name: 'Yes, reject' })).toBeVisible();
  });

  test('the history opens on request', async ({ page }) => {
    await page.goto('/admin/reviews');

    await page.getByRole('button', { name: 'Show past decisions' }).click();

    await expect(page.getByRole('table', { name: 'Past decisions' })).toBeVisible();
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    await page.goto('/admin/reviews');
    await expect(page.getByRole('region', { name: 'Profile picture: Petra Pending' })).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
