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
    const name = `Reader ${info.project.name} ${String(Date.now()).slice(-8)}`;
    await page.goto('/admin/settings/devices');
    const form = page.getByRole('region', { name: 'Add a device' });
    await form.getByRole('textbox', { name: /^Name/ }).fill(name);
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

  test('paired by the code on its screen: the device fetches its key, nobody sees it', async ({
    page,
    playwright,
    baseURL,
  }, info) => {
    const name = `Paired ${info.project.name} ${String(Date.now()).slice(-8)}`;
    const device = await playwright.request.newContext({ baseURL });
    const started = await device.post('/api/v1/device/pair/start', {
      headers: {
        'X-Device-Hardware': '98:A3:16:E5:06:3C',
        'X-Device-Firmware': '0.2.0',
        'X-Device-Types': 'test',
      },
    });
    expect(started.status()).toBe(201);
    const {
      code,
      pairing_id: pairingId,
      url,
    } = (await started.json()) as {
      code: string;
      pairing_id: string;
      url: string;
    };

    // The QR code on the device's screen opens the approval page.
    await page.goto(new URL(url).pathname + new URL(url).search);
    await expect(page.getByRole('region', { name: `Device ${code}` })).toContainText('98:A3:16:E5:06:3C');
    await page.getByRole('textbox', { name: /^Name/ }).fill(name);
    await page.getByRole('button', { name: 'Pair', exact: true }).click();
    await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible();

    const paired = await device.post('/api/v1/device/pair/poll', { data: { pairing_id: pairingId } });
    const answer = (await paired.json()) as { status: string; key: string };
    expect(answer.status).toBe('paired');
    const hello = await device.get('/api/v1/device', { headers: { Authorization: `Bearer ${answer.key}` } });
    expect(await hello.json()).toMatchObject({ name, type: 'test' });
    await device.dispose();

    // Removed again, so the list stays as it was.
    await page.getByRole('button', { name: 'Remove' }).click();
    await page.getByRole('button', { name: 'Yes, remove it' }).click();
    await expect(page).toHaveURL(/\/admin\/settings\/devices$/);
  });

  test('a firmware file uploaded and installed by a device', async ({ page, playwright, baseURL }, info) => {
    // One upload of the one file: both browsers at once would find it uploaded already.
    test.skip(info.project.name !== 'desktop', 'one upload is enough');
    await page.goto('/admin/settings/devices');
    const form = page.getByRole('region', { name: 'Add a device' });
    const name = `Updated ${String(Date.now()).slice(-8)}`;
    await form.getByRole('textbox', { name: /^Name/ }).fill(name);
    await form.getByRole('combobox', { name: 'Type' }).click();
    await page.getByRole('option', { name: 'Test' }).click();
    await form.getByRole('button', { name: 'Add' }).click();
    const key = (await page.getByTestId('device-key').textContent())?.trim() ?? '';

    await page.goto('/admin/settings/devices/firmware');
    await page
      .locator('input[type="file"]')
      .setInputFiles(new URL('../../tests/fixtures/firmware-esptool-esp32s3.bin', import.meta.url).pathname);
    await page.getByRole('button', { name: 'Upload' }).click();
    // The example portal has 1.1.0 already, tried on two devices.
    const uploaded = page
      .getByRole('table', { name: 'Firmware', exact: true })
      .getByRole('row', { name: /0\.3\.0/ });
    await expect(uploaded).toContainText('esp32s3');
    await uploaded.getByRole('button', { name: 'Install on every device' }).click();
    await page.getByRole('button', { name: 'Yes, install 0.3.0' }).click();
    await expect(uploaded).toContainText('Every device');

    const device = await playwright.request.newContext({ baseURL });
    const headers = { Authorization: `Bearer ${key}`, 'X-Device-Firmware': '0.2.0' };
    const hello = (await (await device.get('/api/v1/device', { headers })).json()) as {
      firmware: { version: string; size: number } | null;
    };
    expect(hello.firmware?.version).toBe('0.3.0');
    const file = await device.get('/api/v1/device/firmware/file', { headers });
    expect((await file.body()).length).toBe(hello.firmware?.size);
    await device.get('/api/v1/device', { headers: { ...headers, 'X-Device-Firmware': '0.3.0' } });
    await device.dispose();
    await expect(
      page.getByRole('table', { name: "Devices' firmware" }).getByRole('row', { name: new RegExp(name) }),
    ).toContainText('Up to date');

    // As it was: no update offered, the file and the device gone.
    await uploaded.getByRole('button', { name: 'Stop offering' }).click();
    await page.getByRole('button', { name: 'Yes, stop' }).click();
    await uploaded.getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('button', { name: 'Yes, delete' }).click();
    await expect(uploaded).toHaveCount(0);
    await page.getByRole('link', { name }).click();
    await page.getByRole('button', { name: 'Remove' }).click();
    await page.getByRole('button', { name: 'Yes, remove it' }).click();
    await expect(page).toHaveURL(/\/admin\/settings\/devices$/);
  });

  test('the device protocol is downloaded from the firmware page, with this portal in it', async ({
    page,
  }) => {
    await page.goto('/admin/settings/devices/firmware');
    const waiting = page.waitForEvent('download');
    await page.getByRole('link', { name: 'Device protocol' }).click();
    const download = await waiting;
    expect(download.suggestedFilename()).toBe('device-protocol.md');
    const path = await download.path();
    const { readFile } = await import('node:fs/promises');
    const text = await readFile(path, 'utf-8');
    expect(text).toMatch(/^# Device protocol\n\n> Downloaded from http/);
    expect(text).toContain('### 8.1 Sales point');
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
