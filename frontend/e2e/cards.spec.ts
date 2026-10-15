import { expect, signIn, test } from './fixtures';

// The example portal (scripts/visual/snapshot.py) has a sales point, the
// Rocket fridge, and Anna's student card and key fob linked.

test.describe('cards and sales points', () => {
  test("a member sees their cards, and the reader's place in the menu", async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/account/cards');
    const list = page.getByRole('list', { name: 'Cards' });
    await expect(list.getByRole('listitem', { name: 'Student card' })).toContainText('Rocket fridge');
    await expect(list.getByRole('listitem', { name: 'Key fob' })).toContainText('Off');
    // A new card is linked at a sales point, with its QR code.
    await expect(page.getByText(/tap it on a sales point and scan the QR code/)).toBeVisible();
  });

  test("a lead chooses the team's sales point's menu on the Sales page", async ({ page }) => {
    await signIn(page, 'active@example.org');
    await page.goto('/teams/rocket-team/manage/devices');
    const device = page.getByRole('region', { name: 'Rocket fridge' });
    await expect(device).toContainText('Sales point');
    await expect(device.getByRole('combobox', { name: 'Items' })).toHaveCount(0);

    await page.goto('/teams/rocket-team/manage/sales');
    // The price list first, with what each item sold; the menus on their own tab.
    await expect(
      page.getByRole('table', { name: 'Price list' }).getByRole('row', { name: /^Beer/ }),
    ).toBeVisible();
    await page.getByRole('tab', { name: 'Sales points' }).click();
    const menu = page.getByRole('region', { name: 'Rocket fridge' });
    await expect(menu.getByRole('combobox', { name: 'Items' })).toBeVisible();
    await expect(menu.getByText('Beer, €2.00', { exact: true })).toBeVisible();
  });

  test('an unknown card tapped on a reader is linked with its QR code, on the phone', async ({
    page,
    playwright,
    baseURL,
  }, info) => {
    await signIn(page);
    const name = `QR ${info.project.name} ${String(Date.now()).slice(-8)}`;
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
