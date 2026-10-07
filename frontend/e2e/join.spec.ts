import { expect, signIn, test } from './fixtures';

test.describe('the start page', () => {
  test('says what the portal is, and the two ways in', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByRole('heading', { name: 'Your membership, in one place' })).toBeVisible();
    await page.getByRole('main').getByRole('link', { name: 'Become a member' }).click();
    await expect(page).toHaveURL(/\/join$/);
    await expect(page.getByRole('heading', { name: 'Become a member' })).toBeVisible();
  });

  test('somebody signed in goes on to their account', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/');

    await expect(page).toHaveURL(/\/account$/);
  });
});

test.describe('joining', () => {
  test('the server’s word on each field is shown at it', async ({ page, watched }) => {
    watched.expected.push(/status of 400/);
    await page.goto('/join');

    await page.getByRole('button', { name: 'Join and continue to payment' }).click();

    await expect(page.getByText('Please accept the legal texts to continue.')).toBeVisible();
    await expect(page.getByRole('textbox', { name: /^First name/ })).toHaveAttribute('aria-invalid', 'true');
  });

  test('a text to accept opens over the form, which keeps what was typed', async ({ page }) => {
    await page.goto('/join');
    await page.getByRole('textbox', { name: /^First name/ }).fill('Nora');

    await page.locator('label').getByRole('link', { name: 'Statutes' }).click();
    const dialog = page.getByRole('dialog', { name: 'Statutes' });
    await expect(dialog.getByRole('heading', { name: 'Statuten' })).toBeVisible();
    await page.keyboard.press('Escape');

    await expect(dialog).toBeHidden();
    await expect(page.getByRole('textbox', { name: /^First name/ })).toHaveValue('Nora');
    await expect(page.getByRole('checkbox')).not.toBeChecked();
  });

  test('a new member is signed in and goes on to pay', async ({ page }, testInfo) => {
    await page.goto('/join');

    await page.getByRole('combobox', { name: /^Salutation/ }).click();
    await page.getByRole('option', { name: 'Ms' }).click();
    const fields: [RegExp, string][] = [
      [/^First name/, 'Nora'],
      [/^Last name/, 'Newcomer'],
      [/^Year group/, 'LAV25'],
      [/^Street/, 'Alte Poststraße'],
      [/^House number/, '149'],
      [/^Postal code/, '8020'],
      [/^City/, 'Graz'],
      [/^Private email/, `join-${testInfo.project.name}-${String(Date.now())}@example.org`],
      [/^Private phone/, '+43 316 123456'],
      [
        /^University or company email/,
        `nora.${testInfo.project.name}${String(Date.now())}@edu.fh-joanneum.at`,
      ],
    ];
    for (const [name, value] of fields) {
      await page.getByRole('textbox', { name }).fill(value);
    }
    await page.getByLabel(/^Password/).fill('a-good-password');
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: 'Join and continue to payment' }).click();

    // Stripe is offline here: the membership stands, and My Account says paying can be resumed.
    await expect(page).toHaveURL(/\/account$/);
    await expect(page.getByText(/payment could not be started/)).toBeVisible();
    await expect(page.getByRole('region', { name: 'Membership', exact: true })).toBeVisible();
  });
});

test.describe('a membership for a login without one', () => {
  test('the form has the login’s address', async ({ page }) => {
    await signIn(page);
    await page.goto('/account');

    await page.getByRole('link', { name: 'Become a member' }).click();

    await expect(page).toHaveURL(/\/account\/create-membership$/);
    await expect(page.getByRole('textbox', { name: /^Private email/ })).toHaveValue('admin@example.org');
    await expect(page.getByLabel(/^Password/)).toHaveCount(0);
  });
});

test.describe('back from paying', () => {
  test('thank you, and the way to the account', async ({ page }) => {
    await page.goto('/thank-you?method=checkout&phase=free_period');

    await expect(page.getByText(/free until 31 December/)).toBeVisible();
    await expect(page.getByRole('link', { name: 'Go to My Account' })).toBeVisible();
  });

  test('cancelled: nothing was charged', async ({ page }) => {
    await page.goto('/cancel');

    await expect(page.getByText(/You have not been charged/)).toBeVisible();
  });
});
