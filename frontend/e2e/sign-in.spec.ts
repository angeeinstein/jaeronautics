import { expect, PASSWORD, signIn, test } from './fixtures';

test.describe('signing in', () => {
  test('a wrong password is said, and the right one goes on', async ({ page, watched }) => {
    watched.expected.push(/status of 400/);
    await page.goto('/login');
    await page.getByLabel(/^Email/).fill('active@example.org');
    await page.getByLabel(/^Password/).fill('not-the-password');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page.getByRole('alert')).toHaveText('Invalid email or password.');

    await page.getByLabel(/^Password/).fill(PASSWORD);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();

    await expect(page).toHaveURL(/\/account$/);
  });

  test('asked for by a page, it goes back there', async ({ page }) => {
    await page.goto('/teams');
    await expect(page).toHaveURL(/\/login\?next=/);

    await page.getByLabel(/^Email/).fill('active@example.org');
    await page.getByLabel(/^Password/).fill(PASSWORD);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();

    await expect(page).toHaveURL(/\/teams$/);
  });

  test('coming from the forum, it says the forum is for members', async ({ page }) => {
    await page.goto('/login?next=%2Fforum');

    await expect(page.getByText('The forum is for members')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Become a member' })).toBeVisible();
  });

  test('signing out ends on the start page, which says so', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/account');
    await page.getByRole('button', { name: /Account menu/ }).click();
    await page.getByRole('menuitem', { name: 'Sign out' }).click();

    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByText('You have been signed out', { exact: false })).toBeVisible();
  });

  test('a new password: the same answer for everyone', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByRole('textbox', { name: 'Email' }).fill('nobody@example.org');
    await page.getByRole('button', { name: 'Send the link' }).click();

    await expect(page.getByText(/If we found your account/)).toBeVisible();
  });

  test('a reset link that does not work says so', async ({ page, watched }) => {
    watched.expected.push(/status of 400/);
    await page.goto('/reset-password/not-a-link');

    await expect(page.getByText(/invalid or has expired/)).toBeVisible();
    await expect(page.getByRole('link', { name: 'Ask for a new link' })).toBeVisible();
  });
});
