import { expect, signIn, test } from './fixtures';

test.describe('messages', () => {
  test('a visitor writes through the contact form; an admin replies from the portal', async ({
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
    // Answered here, not from one's own mailbox.
    await expect(message.getByRole('link', { name: 'Answer by email' })).toHaveCount(0);
    await message.getByRole('button', { name: 'Reply' }).click();
    await message.getByRole('textbox', { name: /^Answer to Vera Visitor/ }).fill('Yes, from 14:00.');
    await message.getByRole('button', { name: 'Send' }).click();
    // Answered, it is done: out of the open ones, with its answer kept under it.
    await expect(page.getByRole('region', { name: subject })).toHaveCount(0);
    await page.getByText('All', { exact: true }).click();
    await expect(page.getByRole('region', { name: subject })).toContainText('Yes, from 14:00.');
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
