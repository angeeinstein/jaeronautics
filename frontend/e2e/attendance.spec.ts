import { expect, signIn, test } from './fixtures';

// The example portal (scripts/visual/snapshot.py): attendance on for the
// Rocket Team, Anna's stays -- one of them not checked out -- and Carla
// checked in now. Both tests run on each screen size against the same data,
// so neither undoes what the other needs.
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
    await page.goto('/account/attendance');

    const team = page.getByRole('region', { name: 'Rocket Team' });
    await expect(team.getByRole('table')).toContainText('Hangar');
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

  test('the lead sees who is here and corrects a stay', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/teams/rocket-team/manage/attendance');
    await expect(page.getByRole('region', { name: 'Here now' })).toContainText('Carla');

    await page.getByRole('textbox', { name: 'From' }).fill(tenDaysAgo());
    await page.keyboard.press('Escape');
    // Carla's oldest stay, given a later check-out: the same each time the test runs.
    await page
      .getByRole('table', { name: 'Hours per person' })
      .getByRole('button', { name: /^Carla/ })
      .click();
    const person = page.getByRole('dialog', { name: /^Carla/ });
    await person.getByRole('row').last().getByRole('button', { name: 'Change' }).click();

    const form = page.getByRole('dialog', { name: /^Change a stay of Carla/ });
    // Typed key by key, as the time fields move on by themselves -- and each
    // field seen to hold its value before the next: the picker reads the other
    // field as last drawn, so minutes typed before the hours are drawn leave
    // the time empty (as on a slow machine). A field that did not take the
    // keys -- the dialog still opening on a phone -- is typed into again.
    const type = async (label: string, value: string) => {
      const field = form.getByLabel(label);
      await expect(async () => {
        await field.clear();
        await field.pressSequentially(value, { delay: 50 });
        await expect(field).toHaveValue(value, { timeout: 1000 });
      }).toPass({ timeout: 10_000 });
    };
    await type('Check-out hours', '23');
    await type('Check-out minutes', '40');
    await expect(form.getByRole('button', { name: 'Save' })).toBeEnabled();
    await form.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText('Stay changed.')).toBeVisible();
    await expect(person.getByRole('row').last()).toContainText('23:40');
    await expect(person.getByRole('row').last()).toContainText('Corrected by Anna');
  });

  test('a member adds remote work from the team page, sees it, and deletes it', async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/teams/rocket-team');
    await page.getByRole('button', { name: 'Add remote work' }).click();

    const form = page.getByRole('dialog', { name: 'Add remote work for Rocket Team' });
    // Yesterday, the form's own choice; 20:05 to 21:10, clear of the seeded stays.
    const type = async (label: string, value: string) => {
      const field = form.getByLabel(label);
      await expect(async () => {
        await field.clear();
        await field.pressSequentially(value, { delay: 50 });
        await expect(field).toHaveValue(value, { timeout: 1000 });
      }).toPass({ timeout: 10_000 });
    };
    await type('From, hours', '20');
    await type('From, minutes', '05');
    await type('To, hours', '21');
    await type('To, minutes', '10');
    await form.getByRole('textbox', { name: /What did you do/ }).fill('Battery holder CAD');
    await form.getByRole('button', { name: 'Add' }).click();
    await expect(page.getByText('Added 1 h 5 min.')).toBeVisible();

    await page.goto('/account/attendance');
    const row = page
      .getByRole('region', { name: 'Rocket Team' })
      .getByRole('row')
      .filter({ hasText: 'Battery holder CAD' });
    await expect(row).toContainText('Remote');
    await expect(row).toContainText('1 h 5 min');
    await row.getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('button', { name: 'Yes, delete' }).click();
    await expect(page.getByText('Deleted.')).toBeVisible();
    await expect(row).toHaveCount(0);
  });
});
