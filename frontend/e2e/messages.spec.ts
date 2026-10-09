import { expect, signIn, test } from './fixtures';

test.describe('messages', () => {
  test('a visitor writes through the contact form; the admins read it and mark it done', async ({
    page,
  }, testInfo) => {
    const subject = `Question ${testInfo.project.name} ${String(Date.now())}`;
    await page.goto('/contact');
    await page.getByLabel(/^Name/).fill('Vera Visitor');
    await page.getByLabel(/^Email/).fill('vera@example.net');
    await page.getByRole('combobox', { name: /^About/ }).click();
    await page.getByRole('option', { name: 'Something else' }).click();
    await page.getByLabel(/^Subject/).fill(subject);
    // A person takes a moment; a form sent faster is taken for a bot.
    await page.waitForTimeout(3200);
    await page.getByRole('textbox', { name: /^Message/ }).fill('Is the workshop open on Fridays?');
    await page.getByRole('button', { name: 'Send' }).click();
    await expect(page.getByRole('status')).toContainText('We answer by email');

    await signIn(page);
    await page.goto('/admin/messages');
    const message = page.getByRole('region', { name: subject });
    await expect(message).toContainText('Is the workshop open on Fridays?');
    await expect(message.getByRole('link', { name: 'Answer by email' })).toHaveAttribute(
      'href',
      /^mailto:vera@example\.net\?subject=Re/,
    );
    await message.getByRole('button', { name: 'Mark done' }).click();
    await expect(page.getByRole('region', { name: subject })).toHaveCount(0);
  });

  test('the footer leads to the contact form', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('contentinfo').getByRole('link', { name: 'Contact' }).click();
    await expect(page).toHaveURL(/\/contact$/);
  });

  test('an announcement is counted before it is sent', async ({ page }) => {
    await signIn(page);
    await page.goto('/admin/announcements');
    await expect(page.getByRole('button', { name: /^Send to \d+ (person|people)$/ })).toBeVisible();
    await page.getByText('General assembly', { exact: true }).click();
    await expect(page.getByText('Give the date and time.')).toBeVisible();
  });
});
