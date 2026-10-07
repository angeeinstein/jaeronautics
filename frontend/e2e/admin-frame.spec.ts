import { expect, signIn, test } from './fixtures';

test.describe('the admin frame', () => {
  test('opens on the dashboard with the area and the page marked', async ({ page }) => {
    await signIn(page);
    await page.goto('/admin');

    await expect(page.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeVisible();
    await expect(
      page.getByRole('navigation', { name: 'Areas' }).getByRole('link', { name: 'Admin' }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(page).toHaveTitle(/Dashboard/);
  });

  test('a page not moved yet opens from the sidebar as a whole page', async ({ page, isMobile }) => {
    await signIn(page);
    await page.goto('/admin');
    if (isMobile) await page.getByRole('button', { name: 'Open the menu' }).click();
    const sections = page.getByRole('navigation', { name: 'Sections' }).last();

    await sections.getByRole('link', { name: 'Back to the portal' }).click();

    await expect(page).toHaveURL(/\/account(\?.*)?$/);
    await expect(page.locator('#root')).toHaveCount(0); // Flask's own page, not the app
  });

  test('on a phone the sidebar is a drawer', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    await signIn(page);
    await page.goto('/admin');
    await expect(page.getByRole('navigation', { name: 'Sections' })).toBeHidden();

    await page.getByRole('button', { name: 'Open the menu' }).click();
    const drawer = page.getByRole('dialog');
    await expect(drawer.getByRole('link', { name: 'Dashboard' })).toBeVisible();
    await page.keyboard.press('Escape');

    await expect(drawer).toBeHidden();
  });

  test('the account menu, and logging out', async ({ page }) => {
    await signIn(page);
    await page.goto('/admin');

    await page.getByRole('button', { name: /Account menu/ }).click();
    await expect(page.getByRole('menu')).toContainText(ADMIN_EMAIL);
    await page.getByRole('menuitem', { name: 'Log out' }).click();

    await page.waitForURL((url) => url.pathname === '/');
    await page.goto('/admin');
    await expect(page).toHaveURL(/\/login/);
  });
});

const ADMIN_EMAIL = 'admin@example.org';
