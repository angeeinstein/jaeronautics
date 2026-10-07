import { expect, signIn, test } from './fixtures';

test.describe('my account', () => {
  test('a member: the membership, the forum, the addresses, the forms', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/account');

    await expect(page.getByRole('heading', { name: 'My Account', level: 1 })).toBeVisible();
    for (const name of ['Membership', 'Forum', 'Contact details', 'Your data']) {
      await expect(page.getByRole('region', { name, exact: true })).toBeVisible();
    }
    // The addresses once: in the contact details, each with whether it is confirmed.
    await expect(page.getByRole('region', { name: 'Email addresses' })).toHaveCount(0);
    await expect(
      page.getByRole('region', { name: 'Contact details' }).getByText('Confirmed', { exact: true }),
    ).toHaveCount(2);
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
    await signIn(page, 'photo-needed@example.org');
    await page.goto('/forum?token=not-a-link');

    await expect(page.getByRole('heading', { name: 'Forum', level: 1 })).toBeVisible();
    await expect(page.getByText('This forum access link is invalid or has expired.')).toBeVisible();
  });

  test('a deletion link that does not work says so, and offers nothing to click', async ({
    page,
    watched,
  }) => {
    watched.expected.push(/status of 400/);
    await signIn(page, 'active@example.org');
    await page.goto('/account/delete/not-a-link');

    await expect(page.getByText(/This deletion link is invalid or has expired/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Delete my account' })).toHaveCount(0);
  });

  test('a profile picture: chosen, framed, sent for review', async ({ page }) => {
    await signIn(page, 'photo-needed@example.org');
    await page.goto('/forum');
    // A small PNG made by the browser itself.
    const png = await page.evaluate(async () => {
      const canvas = document.createElement('canvas');
      canvas.width = 120;
      canvas.height = 80;
      const context = canvas.getContext('2d');
      if (context) {
        context.fillStyle = '#c33';
        context.fillRect(0, 0, 120, 80);
      }
      const blob = await new Promise<Blob | null>((resolve) => {
        canvas.toBlob(resolve, 'image/png');
      });
      return Array.from(new Uint8Array(await (blob ?? new Blob()).arrayBuffer()));
    });

    await page
      .locator('input[type="file"]')
      .setInputFiles({ name: 'me.png', mimeType: 'image/png', buffer: Buffer.from(png) });
    await expect(page.getByRole('application', { name: /Your photo, as it will be cut/ })).toBeVisible();
    await page.getByRole('button', { name: 'Zoom in' }).click();
    await page.getByRole('button', { name: 'Send for review' }).click();

    await expect(page.getByText(/Your picture is waiting for review/)).toBeVisible();
    await expect(page.getByRole('img', { name: 'Your picture, waiting for review' })).toBeVisible();
  });

  test('the password: the new one twice', async ({ page }) => {
    await signIn(page, 'returning@example.org');
    await page.goto('/account');
    // From the menu at the top right: the one place for it.
    await page.getByRole('button', { name: /^Account menu for/ }).click();
    await page.getByRole('menuitem', { name: 'Change password' }).click();

    await expect(page).toHaveURL(/\/change-password$/);
    await page.getByLabel(/^Current password/).fill('snapshot-password');
    await page.getByLabel(/^New password(?! again)/).fill('another-password-1');
    await page.getByLabel(/^New password again/).fill('another-password-2');
    await page.getByRole('button', { name: 'Change password' }).click();

    await expect(page.getByText('The two new passwords are not the same.')).toBeVisible();
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
