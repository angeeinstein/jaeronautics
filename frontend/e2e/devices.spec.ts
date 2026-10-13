import { expect, signIn, test } from './fixtures';

test.describe('devices', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test('added on the page, its key works for the device, which then shows online', async ({
    page,
    playwright,
    baseURL,
  }, info) => {
    const name = `E2E reader ${info.project.name} ${String(Date.now())}`;
    await page.goto('/admin/settings/devices');
    const form = page.getByRole('region', { name: 'Add a device' });
    await form.getByRole('textbox', { name: 'Name' }).fill(name);
    await form.getByRole('combobox', { name: 'Type' }).click();
    await page.getByRole('option', { name: 'Test' }).click();
    await form.getByRole('button', { name: 'Add' }).click();

    const key = (await page.getByTestId('device-key').textContent())?.trim() ?? '';
    expect(key).toMatch(/^jadev_[0-9a-f]{12}_/);

    // The device: a client of its own, with no session or cookies.
    const device = await playwright.request.newContext({ baseURL });
    const hello = await device.get('/api/v1/device', {
      headers: { Authorization: `Bearer ${key}`, 'X-Device-Firmware': '0.1.0' },
    });
    expect(hello.status()).toBe(200);
    expect(await hello.json()).toMatchObject({ name, type: 'test', settings: { greeting: 'Hello' } });
    const ping = await device.post('/api/v1/device/events', {
      headers: { Authorization: `Bearer ${key}` },
      data: { event_id: 'e2e-1', kind: 'ping', data: { n: 1 } },
    });
    expect((await ping.json()) as unknown).toEqual({
      event_id: 'e2e-1',
      answer: { result: 'ok', message: 'Hello', echo: { n: 1 } },
    });
    await device.dispose();

    await page.getByRole('button', { name: 'Done' }).click();
    await page.getByRole('table', { name: 'Devices' }).getByRole('link', { name }).click();
    await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible();
    await expect(page.getByRole('region', { name: 'State' })).toContainText('Online');
    await expect(page.getByRole('region', { name: 'State' })).toContainText('0.1.0');
    await expect(page.getByRole('table', { name: 'Recent events' })).toContainText('Reported “ping”');
    const requests = page.getByRole('table', { name: 'Recent requests' });
    await expect(requests).toContainText('POST /device/events');
    await expect(requests).toContainText('GET /device');
    await expect(page.getByRole('region', { name: 'Key' })).toContainText(key.slice(-4));

    // Removed again, so the list stays as it was.
    await page.getByRole('button', { name: 'Remove' }).click();
    await page.getByRole('button', { name: 'Yes, remove it' }).click();
    await expect(page).toHaveURL(/\/admin\/settings\/devices$/);
    await expect(page.getByRole('link', { name })).toHaveCount(0);
  });

  test('a key it does not know is refused', async ({ playwright, baseURL }) => {
    const device = await playwright.request.newContext({ baseURL });
    const answer = await device.get('/api/v1/device', {
      headers: { Authorization: 'Bearer jadev_000000000000_nothing' },
    });
    expect(answer.status()).toBe(401);
    expect(await answer.json()).toMatchObject({ error: { code: 'device_key_unknown' } });
    await device.dispose();
  });
});
