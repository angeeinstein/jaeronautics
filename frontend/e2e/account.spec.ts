import { expect, signIn, test } from './fixtures';

test.describe('my account', () => {
  test('a member: the overview, and a page for each part', async ({ page, isMobile }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/account');

    await expect(
      page.getByRole('heading', { name: 'Anna Maximilian-Hofstetter-Wallensteiner', level: 1 }),
    ).toBeVisible();
    const tiles = page.getByRole('main');
    await expect(tiles.getByRole('link', { name: 'Forum and picture' })).toHaveAttribute(
      'href',
      '/account/forum',
    );
    await expect(tiles.getByRole('link', { name: 'Teams', exact: true })).toHaveAttribute('href', '/teams');
    await tiles.getByRole('link', { name: 'Membership', exact: true }).click();
    await expect(page).toHaveURL(/\/account\/membership$/);
    await expect(page.getByRole('region', { name: 'Membership', exact: true })).toBeVisible();

    // The side menu, on a phone in the drawer.
    if (isMobile) await page.getByRole('button', { name: 'Open the menu' }).click();
    await page
      .getByRole('navigation', { name: 'Sections' })
      .last()
      .getByRole('link', { name: 'Profile' })
      .click();
    await expect(page).toHaveURL(/\/account\/profile$/);
    // The addresses once: in the contact details, each with whether it is confirmed.
    await expect(
      page.getByRole('region', { name: 'Contact details' }).getByText('Confirmed', { exact: true }),
    ).toHaveCount(2);
    await expect(page.getByRole('region', { name: 'Name and membership type' })).toBeVisible();

    await page.goto('/account/data');
    await expect(page.getByRole('region', { name: 'Your data', exact: true })).toBeVisible();
    await expect(page.getByRole('contentinfo').getByRole('link', { name: 'Impressum' })).toBeVisible();
  });

  test('contact details: read, changed, saved, read again', async ({ page }) => {
    await signIn(page, 'photo-needed@example.org');
    await page.goto('/account/profile');
    const contact = page.getByRole('region', { name: 'Contact details' });

    await contact.getByRole('button', { name: 'Edit' }).click();
    await contact.getByRole('textbox', { name: 'Work phone' }).fill('+43 316 5453');
    await contact.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByText('Saved.')).toBeVisible();
    await expect(contact.getByText('+43 316 5453')).toBeVisible();
    await page.reload();
    await expect(contact.getByText('+43 316 5453')).toBeVisible();
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
    const member = ['/account', '/account/profile', '/account/membership', '/account/forum', '/account/data'];
    // Without a membership there is only the overview and the data.
    const pages = { active: member, returning: member, admin: ['/account', '/account/data'] };
    for (const [who, paths] of Object.entries(pages)) {
      await page.context().clearCookies();
      await signIn(page, `${who}@example.org`);
      for (const path of paths) {
        await page.goto(path);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, `${who} ${path}`).toBeLessThanOrEqual(0);
      }
    }
  });
});
