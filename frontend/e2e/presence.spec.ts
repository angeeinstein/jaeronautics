import { expect, signIn, test } from './fixtures';

test.describe('who is using the portal, before an update', () => {
  test('somebody typing on the join page shows on the Updates page', async ({ page, browser }, testInfo) => {
    // Somebody else, not signed in, in a browser of their own, starts joining.
    const other = await browser.newContext({ baseURL: testInfo.project.use.baseURL });
    const visitor = await other.newPage();
    await visitor.goto('/join');
    await visitor.getByRole('radio').first().click();
    await expect(visitor.getByRole('heading', { name: 'About you' })).toBeVisible();
    // The click was said at once; the typing is said within seconds, while the page is in front.
    const said = visitor.waitForRequest((request) => request.url().endsWith('/api/v1/presence'), {
      timeout: 20_000,
    });
    await visitor.getByRole('textbox').first().fill('Ann');
    await said;

    await signIn(page);
    await page.goto('/admin/settings/updates');
    const install = page.getByRole('region', { name: 'Install' });
    await expect(install.getByText(/using the portal right now/)).toBeVisible();
    await expect(install.getByText(/typing/).first()).toBeVisible();
    await expect(install.getByText('/join', { exact: true })).toBeVisible();
    await other.close();
  });
});
