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
    // What it sells is not chosen here: on the owner's Sales page.
    await expect(form.getByRole('combobox', { name: 'Items' })).toHaveCount(0);
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
      data: { event_id: `link-${String(Date.now())}`, kind: 'card', data: { card } },
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

  test("a lead chooses the team's sales point's menu on the Sales page", async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/teams/rocket-team/manage/devices');
    const device = page.getByRole('region', { name: 'Rocket fridge' });
    await expect(device).toContainText('Sales point');
    await expect(device.getByRole('combobox', { name: 'Items' })).toHaveCount(0);

    await page.goto('/teams/rocket-team/manage/sales');
    const menu = page.getByRole('region', { name: 'Rocket fridge' });
    await expect(menu.getByRole('combobox', { name: 'Items' })).toBeVisible();
    await expect(menu.getByText('Beer, €2.00', { exact: true })).toBeVisible();
    await expect(
      page.getByRole('table', { name: 'Sold by item' }).getByRole('row', { name: /Beer/ }),
    ).toBeVisible();
  });

  test('an unknown card tapped on a reader is linked with its QR code, on the phone', async ({
    page,
    playwright,
    baseURL,
  }, info) => {
    await signIn(page);
    const name = `E2E QR reader ${info.project.name} ${String(Date.now())}`;
    await page.goto('/admin/settings/devices');
    const form = page.getByRole('region', { name: 'Add a device' });
    await form.getByRole('textbox', { name: /^Name/ }).fill(name);
    await form.getByRole('combobox', { name: 'Type' }).click();
    await page.getByRole('option', { name: 'Sales point' }).click();
    await form.getByRole('button', { name: 'Add' }).click();
    const key = (await page.getByTestId('device-key').textContent())?.trim() ?? '';

    // The reader: an unknown card, answered with the address for a QR code.
    const reader = await playwright.request.newContext({ baseURL });
    const headers = { Authorization: `Bearer ${key}` };
    const card = info.project.name === 'phone' ? '04E2E0000000A1' : '04E2E0000000A2';
    const tapped = await reader.post('/api/v1/device/events', {
      headers,
      data: { event_id: `qr-${String(Date.now())}`, kind: 'card', data: { card } },
    });
    const { answer } = (await tapped.json()) as {
      answer: { reason: string; link: { id: number; url: string } };
    };
    expect(answer.reason).toBe('card_unknown');
    const asked = async () =>
      (
        (await (
          await reader.get(`/api/v1/device/card-links/${String(answer.link.id)}`, { headers })
        ).json()) as {
          state: string;
        }
      ).state;
    expect(await asked()).toBe('waiting');

    // The phone opens it: the reader moves on; linked to the account signed in.
    await page.goto(new URL(answer.link.url).pathname);
    await expect(page.getByText(`Card …${card.slice(-4)}`)).toBeVisible();
    expect(await asked()).toBe('opened');
    await page.getByRole('textbox', { name: /^Name/ }).fill('QR card');
    await page.getByRole('button', { name: 'Link to my account' }).click();
    await expect(page.getByRole('status')).toContainText('QR card');
    expect(await asked()).toBe('linked');
    await reader.dispose();

    // Removed again, with the reader, so the example stays as it was.
    await page.getByRole('link', { name: 'My cards' }).click();
    const linked = page.getByRole('listitem', { name: 'QR card' });
    await linked.getByRole('button', { name: 'Remove' }).click();
    await linked.getByRole('button', { name: 'Yes, remove' }).click();
    await expect(linked).toHaveCount(0);
    await page.goto('/admin/settings/devices');
    await page.getByRole('table', { name: 'Devices' }).getByRole('link', { name }).click();
    await page.getByRole('button', { name: 'Remove' }).click();
    await page.getByRole('button', { name: 'Yes, remove it' }).click();
    await expect(page).toHaveURL(/\/admin\/settings\/devices$/);
  });
});
