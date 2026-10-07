import { expect, signIn, test } from './fixtures';

test.describe('my account', () => {
  test('a member: the membership, the forum, the addresses, the forms', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/account');

    await expect(page.getByRole('heading', { name: 'My Account', level: 1 })).toBeVisible();
    for (const name of ['Membership', 'Forum', 'Email addresses', 'Contact details', 'Your data']) {
      await expect(page.getByRole('region', { name, exact: true })).toBeVisible();
    }
    await expect(page.getByRole('contentinfo').getByRole('link', { name: 'Impressum' })).toBeVisible();
  });

  test('contact details are saved, and the page shows them', async ({ page }) => {
    await signIn(page, 'photo-needed@example.org');
    await page.goto('/account');
    const contact = page.getByRole('region', { name: 'Contact details' });

    const phone = contact.getByRole('textbox', { name: 'Work phone' });
    await phone.fill('+43 316 5453');
    await contact.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByText('Saved.')).toBeVisible();
    await page.reload();
    await expect(contact.getByRole('textbox', { name: 'Work phone' })).toHaveValue('+43 316 5453');
  });

  test('what Flask said before sending the browser here is shown', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/account/delete/not-a-link');

    await expect(page).toHaveURL(/\/account$/);
    await expect(
      page.getByText('This deletion link is invalid or has expired.', { exact: false }),
    ).toBeVisible();
  });

  test('an account without a membership', async ({ page }) => {
    await signIn(page, 'admin@example.org');
    await page.goto('/account');

    await expect(page.getByText('This account has no membership.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Become a member' })).toBeVisible();
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    for (const who of ['active', 'returning', 'admin']) {
      await page.context().clearCookies();
      await signIn(page, `${who}@example.org`);
      await page.goto('/account');
      await expect(page.getByRole('heading', { name: 'My Account', level: 1 })).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, who).toBeLessThanOrEqual(0);
    }
  });
});
