import { expect, signIn, test } from './fixtures';

// The example portal (scripts/visual/snapshot.py): attendance on for the
// Rocket Team, Anna's stays -- one of them not checked out -- and Carla
// checked in now. In order: the lead corrects the stay the member asked about.
test.describe.configure({ mode: 'serial' });

/** Ten days ago, as the portal writes a day: far enough back for every seeded stay. */
function tenDaysAgo(): string {
  const day = new Date(Date.now() - 10 * 86_400_000);
  const two = (n: number) => String(n).padStart(2, '0');
  return `${two(day.getUTCDate())}.${two(day.getUTCMonth() + 1)}.${day.getUTCFullYear()}`;
}

test.describe('attendance', () => {
  test('a member sees their stays and asks the leads to correct one not checked out', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/account');
    await page.getByRole('navigation').getByRole('link', { name: 'Attendance' }).click();

    const team = page.getByRole('region', { name: 'Rocket Team' });
    await expect(team.getByRole('table')).toContainText('Hangar door');
    await expect(team.getByRole('table')).toContainText('Not checked out');
    await team.getByRole('button', { name: 'Ask for a correction' }).click();
    const dialog = page.getByRole('dialog', { name: 'Message the leads of Rocket Team' });
    await expect(dialog.getByRole('textbox', { name: 'Subject' })).toHaveValue(
      /^Attendance on \d\d\.\d\d\.\d{4}$/,
    );
    await expect(dialog.getByRole('textbox', { name: 'Message' })).toHaveValue(
      /not checked out\. I left at $/,
    );
  });

  test('the lead sees who is here and corrects the stay', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/teams/rocket-team/manage/attendance');
    await expect(page.getByRole('region', { name: 'Here now' })).toContainText('Carla');

    await page.getByRole('textbox', { name: 'From' }).fill(tenDaysAgo());
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Anna Active' }).click();
    const person = page.getByRole('dialog', { name: 'Anna Active' });
    const forgotten = person.getByRole('row').filter({ hasText: 'Not checked out' });
    await forgotten.getByRole('button', { name: 'Change' }).click();

    const form = page.getByRole('dialog', { name: 'Change a stay of Anna Active' });
    await form.getByLabel('Check-out hours').fill('23');
    await form.getByLabel('Check-out minutes').fill('40');
    await form.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText('Stay changed.')).toBeVisible();
    await expect(person.getByRole('row').filter({ hasText: 'Not checked out' })).toHaveCount(0);
    await expect(person).toContainText('Corrected by Anna Active');
  });
});
