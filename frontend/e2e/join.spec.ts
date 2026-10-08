import type { Page } from '@playwright/test';

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

/** Which kind of member: the next step comes by itself. */
async function pick(page: Page, kind: RegExp) {
  await page.getByRole('radio', { name: kind }).click();
  await expect(page.getByRole('heading', { name: 'About you' })).toBeVisible();
}

async function next(page: Page, heading: string) {
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { name: heading })).toBeVisible();
}

async function fill(page: Page, fields: [RegExp | string, string][]) {
  for (const [name, value] of fields) {
    await page.getByRole('textbox', { name, exact: true }).fill(value);
  }
}

test.describe('joining', () => {
  test('each step asks only what it needs, said at the field', async ({ page }) => {
    await page.goto('/join');

    await pick(page, /^Student/);
    await page.getByRole('button', { name: 'Continue' }).click();

    await expect(page.getByText('Please choose your programme and the year you started.')).toBeVisible();
    await expect(page.getByRole('textbox', { name: /^First name/ })).toBeFocused();
    await page.getByRole('button', { name: 'Back' }).click();
    await pick(page, /^Company or partner/);
    await expect(page.getByRole('radio', { name: /^Aviation/ })).toHaveCount(0);
    await expect(page.getByRole('textbox', { name: 'Company', exact: true })).toBeVisible();
  });

  test('a new member goes through the steps, sees what it costs, is signed in and goes on to pay', async ({
    page,
  }, testInfo) => {
    const stamp = `${testInfo.project.name}${String(Date.now())}`;
    await page.goto('/join');

    await pick(page, /^Student/);
    await page.getByRole('radio', { name: 'Ms' }).check();
    await fill(page, [
      [/^First name/, 'Nora'],
      [/^Last name/, 'Newcomer'],
      [/^University email/, `nora.${stamp}@edu.fh-joanneum.at`],
    ]);
    await page.getByRole('radio', { name: /^Aviation · Bachelor/ }).click();
    await page
      .locator('label')
      .filter({ hasText: /^2025$/ })
      .click();
    await expect(page.getByText('LAV25')).toBeVisible();
    await expect(page.getByText('NewcomerN_L25')).toBeVisible();
    await next(page, 'How we reach you');
    await fill(page, [
      [/^Private email/, `join-${stamp}@example.org`],
      [/^Phone/, '+43 316 123456'],
      [/^Street/, 'Alte Poststraße'],
      [/^House number/, '149'],
      [/^Postal code/, '8020'],
      [/^City/, 'Graz'],
    ]);
    await next(page, 'Your password');
    await page.getByLabel(/^Password/).fill('a-good-password');
    await next(page, 'Check and join');

    await expect(page.getByRole('region', { name: 'What it costs' })).toContainText('€40.00');
    // A text to accept opens over the form, which keeps where it was.
    await page.locator('label').getByRole('link', { name: 'Statutes' }).click();
    const dialog = page.getByRole('dialog', { name: 'Statutes' });
    await expect(dialog.getByRole('heading', { name: 'Statuten' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: 'Join and continue to payment' }).click();

    // Stripe is offline here: the membership stands, and My Account says paying can be resumed.
    await expect(page).toHaveURL(/\/account$/);
    await expect(page.getByText(/payment could not be started/)).toBeVisible();
    await expect(page.getByRole('link', { name: 'Membership', exact: true })).toBeVisible();
  });

  test('the server’s word on a field takes the form back to its step', async ({
    page,
    watched,
  }, testInfo) => {
    watched.expected.push(/status of 409/);
    await page.goto('/join');

    await pick(page, /^Alumni/);
    await page.getByRole('radio', { name: 'Mr' }).check();
    await fill(page, [
      [/^First name/, 'Anton'],
      [/^Last name/, 'Again'],
    ]);
    await next(page, 'How we reach you');
    await fill(page, [
      // Has an account already.
      [/^Private email/, 'active@example.org'],
      [/^Phone/, '+43 316 123456'],
      [/^Street/, 'Main'],
      [/^House number/, '1'],
      [/^Postal code/, '8010'],
      [/^City/, 'Graz'],
    ]);
    await next(page, 'Your password');
    await page.getByLabel(/^Password/).fill(`another-${testInfo.project.name}`);
    await next(page, 'Check and join');
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: 'Join and continue to payment' }).click();

    const alert = page.getByRole('alert');
    await expect(alert).toContainText('already exists');
    await expect(alert.getByRole('link', { name: 'Sign in' })).toBeVisible();
  });

  test('on a phone nothing sticks out of any step', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    await page.goto('/join');
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

    expect(await overflow()).toBeLessThanOrEqual(0);
    await pick(page, /^Company or partner/);
    expect(await overflow()).toBeLessThanOrEqual(0);
    await page.getByRole('radio', { name: 'Ms' }).check();
    await fill(page, [
      [/^First name/, 'Nora'],
      [/^Last name/, 'Company'],
      ['Company', 'Example Aero GmbH'],
    ]);
    await next(page, 'How we reach you');
    expect(await overflow()).toBeLessThanOrEqual(0);
  });
});

test.describe('a membership for a login without one', () => {
  test('the form has the login’s address', async ({ page }) => {
    await signIn(page);
    await page.goto('/account');

    await page.getByRole('link', { name: 'Become a member' }).click();

    await expect(page).toHaveURL(/\/account\/create-membership$/);
    await expect(page.getByRole('navigation', { name: 'Steps' })).not.toContainText('Password');
    await pick(page, /^Staff/);
    await page.getByRole('radio', { name: 'Mr' }).check();
    await fill(page, [
      [/^First name/, 'Adam'],
      [/^Last name/, 'Admin'],
      [/^Institute email/, 'adam.admin@fh-joanneum.at'],
    ]);
    await next(page, 'How we reach you');
    await expect(page.getByRole('textbox', { name: /^Sign-in email/ })).toHaveValue('admin@example.org');
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
