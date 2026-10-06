import { expect, signIn, test } from './fixtures';

test.describe('legal texts', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test('the texts in force, each with its PDF, made first and then opened', async ({ page }) => {
    await page.goto('/admin/legal');

    const inForce = page.getByRole('region', { name: 'In force', exact: true });
    await expect(inForce.getByRole('link').first()).toBeVisible();
    const pdf = inForce.getByRole('link', { name: 'PDF' }).first();
    const [made, opened] = await Promise.all([
      page.waitForResponse((response) => response.url().includes('prepare=1')),
      page.context().waitForEvent('request', (request) => /[?&]v=/.test(request.url())),
      pdf.click(),
    ]);

    // Made first, then opened at an address carrying the PDF's hash.
    expect(made.ok()).toBe(true);
    const answer = await opened.response();
    expect(answer?.headers()['content-type']).toBe('application/pdf');
  });

  test('the template, uploaded as it is, comes back as its PDF', async ({ page }) => {
    await page.goto('/admin/legal');
    const template = await page.request.get('/admin/legal/template');
    const name =
      /filename="([^"]+)"/.exec(template.headers()['content-disposition'] ?? '')?.[1] ?? 'template.md';

    await page
      .locator('input[type="file"]')
      .first()
      .setInputFiles({ name, mimeType: 'text/markdown', buffer: await template.body() });
    const [made] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/api/v1/admin/legal/preview'), {
        timeout: 60_000,
      }),
      page.waitForEvent('popup', { timeout: 60_000 }),
      page.getByRole('button', { name: 'Make the PDF' }).click(),
    ]);

    expect(made.status()).toBe(200);
    expect(made.headers()['content-type']).toBe('application/pdf');
  });

  test('on a phone nothing sticks out of the page', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'phones only');
    await page.goto('/admin/legal');
    await expect(page.getByRole('region', { name: 'In force', exact: true })).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
