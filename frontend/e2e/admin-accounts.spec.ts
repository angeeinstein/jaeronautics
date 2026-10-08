import { expect, signIn, test } from './fixtures';

test.describe('the account list', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test('everybody, with their membership', async ({ page }) => {
    await page.goto('/admin/accounts');

    const table = page.getByRole('table', { name: 'Accounts' });
    await expect(page.getByRole('heading', { name: 'Accounts', level: 1 })).toBeVisible();
    await expect(table.getByRole('row', { name: /Carla Cancel/ })).toContainText('Ending');
    await expect(table.getByRole('row', { name: /Fritz Failed/ })).toContainText('Payment failed');
    await expect(table.getByRole('row', { name: /BackB_L21/ })).toContainText('Old forum');
  });

  test('a chip narrows it, and the address keeps it', async ({ page }) => {
    await page.goto('/admin/accounts');

    // The chip's label is what is seen and clicked; its radio button is hidden.
    await page
      .getByRole('radiogroup', { name: 'Membership' })
      .locator('label', { hasText: /^Ending/ })
      .click();

    await expect(page).toHaveURL(/\/admin\/accounts\?membership=ending$/);
    const rows = page.getByRole('table', { name: 'Accounts' }).getByRole('row');
    await expect(rows).toHaveCount(2); // the heading and Carla
    await page.reload();
    await expect(page.getByRole('radio', { name: /^Ending/ })).toBeChecked();
  });

  test('the search follows what is typed', async ({ page }) => {
    await page.goto('/admin/accounts');

    await page.getByRole('textbox', { name: 'Search accounts' }).fill('sepa');

    await expect(page).toHaveURL(/q=sepa/);
    await expect(page.getByRole('table', { name: 'Accounts' }).getByRole('row')).toHaveCount(2);
  });

  test('a heading sorts the list', async ({ page }) => {
    await page.goto('/admin/accounts');

    await page.getByRole('button', { name: /Membership/ }).click();

    await expect(page).toHaveURL(/sort=membership&dir=asc/);
    await expect(page.getByRole('columnheader', { name: /Membership/ })).toHaveAttribute(
      'aria-sort',
      'ascending',
    );
  });

  test('a link to the old page still finds the same people', async ({ page }) => {
    await page.goto('/admin/accounts?membership_status=cancel_scheduled');

    await expect(page).toHaveURL(/\/admin\/accounts\?membership=ending$/);
  });

  test('a row opens the account', async ({ page }) => {
    await page.goto('/admin/accounts?q=carla');

    // Anywhere in the row, not only on the name.
    await page
      .getByRole('table', { name: 'Accounts' })
      .getByRole('row', { name: /Carla Cancel/ })
      .getByRole('cell')
      .nth(2)
      .click();

    await expect(page).toHaveURL(/\/admin\/accounts\/\d+$/);
    await expect(page.getByRole('heading', { name: 'Carla Cancel', level: 1 })).toBeVisible();
  });

  test('on a phone the table scrolls, not the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    await page.goto('/admin/accounts');
    await expect(page.getByRole('table', { name: 'Accounts' })).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
