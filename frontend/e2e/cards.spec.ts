import { expect, signIn, test } from './fixtures';

// The example portal (scripts/visual/snapshot.py) has a sales point, the
// Rocket fridge, and Anna's student card and key fob linked.

test.describe('cards and sales points', () => {
  test('a reader added as a sales point links a card tapped while an admin waits', async ({
    page,
    playwright,
    baseURL,
  }, info) => {
    await signIn(page);
    const name = `E2E fridge ${info.project.name} ${String(Date.now())}`;
    await page.goto('/admin/settings/devices');
    const form = page.getByRole('region', { name: 'Add a device' });
    await form.getByRole('textbox', { name: /^Name/ }).fill(name);
    await form.getByRole('combobox', { name: 'Owner' }).click();
    await page.getByRole('option', { name: 'Rocket Team' }).click();
    await form.getByRole('combobox', { name: 'Type' }).click();
    await page.getByRole('option', { name: 'Sales point' }).click();
    await form.getByRole('combobox', { name: 'Item' }).click();
    await page.getByRole('option', { name: 'Mate, €2.50' }).click();
    await form.getByRole('button', { name: 'Add' }).click();
    const key = (await page.getByTestId('device-key').textContent())?.trim() ?? '';
    expect(key).toMatch(/^jadev_/);

    // Carla is beside the admin, with a card no account has yet.
    await page.goto('/admin/cards');
    await page.getByRole('table', { name: 'Cards' }).getByRole('link', { name: /Carla/ }).first().click();
    await expect(page.getByRole('tab', { name: 'Cards', selected: true })).toBeVisible();
    await page.getByRole('combobox', { name: 'Reader' }).click();
    await page.getByRole('option', { name: new RegExp(name) }).click();
    await page.getByRole('button', { name: 'Wait for a tap' }).click();
    await expect(page.getByRole('status')).toContainText(`Tap the card on ${name}`);

    const reader = await playwright.request.newContext({ baseURL });
    const card = info.project.name === 'phone' ? '04E2E000000001' : '04E2E000000002';
    const tap = await reader.post('/api/v1/device/events', {
      headers: { Authorization: `Bearer ${key}` },
      data: { event_id: `link-${String(Date.now())}`, kind: 'tap', data: { card } },
    });
    expect((await tap.json()) as unknown).toMatchObject({ answer: { result: 'linked', card: 'Card' } });
    await reader.dispose();

    const linked = page.getByRole('listitem', { name: 'Card' }).filter({ hasText: card.slice(-4) });
    await expect(linked).toBeVisible({ timeout: 10_000 });

    // Removed again, with the reader, so the example stays as it was.
    await linked.getByRole('button', { name: 'Remove' }).click();
    await linked.getByRole('button', { name: 'Yes, remove' }).click();
    await expect(linked).toHaveCount(0);
    await page.goto('/admin/settings/devices');
    await page.getByRole('table', { name: 'Devices' }).getByRole('link', { name }).click();
    await page.getByRole('button', { name: 'Remove' }).click();
    await page.getByRole('button', { name: 'Yes, remove it' }).click();
    await expect(page).toHaveURL(/\/admin\/settings\/devices$/);
  });

  test("a member sees their cards, and the reader's place in the menu", async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/account/cards');
    const list = page.getByRole('list', { name: 'Cards' });
    await expect(list.getByRole('listitem', { name: 'Student card' })).toContainText('Rocket fridge');
    await expect(list.getByRole('listitem', { name: 'Key fob' })).toContainText('Off');
    // No NFC in the test browser: it says where a card can be linked instead.
    await expect(page.getByText(/This browser cannot read cards/)).toBeVisible();
  });

  test("a lead runs the team's reader on the team's page", async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/teams/rocket-team/manage/devices');
    const fridge = page.getByRole('region', { name: 'Rocket fridge' });
    await expect(fridge).toContainText('Sales point');
    await expect(fridge.getByRole('combobox', { name: 'Item' })).toHaveValue('Beer, €2.00');
  });
});
