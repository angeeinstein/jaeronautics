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
      page.getByRole('button', { name: 'Preview PDF' }).click(),
    ]);

    expect(made.status()).toBe(200);
    expect(made.headers()['content-type']).toBe('application/pdf');
  });

  test('the template uploaded as a draft is listed, and deleted again', async ({ page }) => {
    await page.goto('/admin/legal');
    const template = await page.request.get('/admin/legal/template');

    await page
      .locator('input[type="file"]')
      .first()
      .setInputFiles({ name: 'anything.md', mimeType: 'text/markdown', buffer: await template.body() });
    await page.getByRole('button', { name: 'Upload as draft' }).click();

    const waiting = page.getByRole('region', { name: 'Not in force yet', exact: true });
    const draft = waiting.getByRole('listitem').filter({ hasText: 'Mustertext' });
    await expect(draft).toContainText('Uploaded');
    await draft.getByRole('button', { name: 'Delete' }).click();
    await draft.getByRole('button', { name: 'Yes, delete' }).click();
    await expect(draft).toHaveCount(0);
  });

  test('a new version written in the editor saves itself, is discarded, and deleted again', async ({
    page,
  }) => {
    test.slow();
    await page.goto('/admin/legal');
    const panel = page.getByRole('region', { name: 'Write a new version', exact: true });
    await panel.getByLabel('Text', { exact: true }).click();
    await page.getByRole('option', { name: 'Impressum' }).click();
    await panel.getByRole('button', { name: 'Write' }).click();
    await page.waitForURL(/\/admin\/legal\/drafts\/\d+$/);

    const text = page.getByRole('textbox', { name: 'German text, Markdown' });
    await text.click();
    await page.keyboard.press('Control+End');
    await page.keyboard.type('\n\nEin neuer Absatz aus dem Test.');
    await expect(page.getByText(/^Saved/)).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: 'Discard changes' }).click();
    await expect(text).not.toContainText('Ein neuer Absatz aus dem Test.');
    await expect(page.getByText(/^Saved/)).toBeVisible({ timeout: 10_000 });

    await page.goto('/admin/legal');
    const draft = page
      .getByRole('region', { name: 'Not in force yet', exact: true })
      .getByRole('listitem')
      .filter({ hasText: 'Impressum' })
      .filter({ hasText: 'Uploaded' });
    await draft.getByRole('button', { name: 'Delete' }).click();
    await draft.getByRole('button', { name: 'Yes, delete' }).click();
    await expect(draft).toHaveCount(0);
  });

  test("one person edits a draft at a time: the team's lead sees the admin editing it", async ({
    page,
    browser,
    isMobile,
  }) => {
    test.skip(isMobile, 'two browsers once is enough');
    test.slow();
    await page.goto('/admin/legal');
    const panel = page.getByRole('region', { name: 'Write a new version', exact: true });
    await panel.getByLabel('Text', { exact: true }).click();
    await page.getByRole('option', { name: /Teamordnung · Rocket/ }).click();
    await panel.getByRole('button', { name: 'Write' }).click();
    await page.waitForURL(/\/admin\/legal\/drafts\/\d+$/);
    const recordId = /drafts\/(\d+)$/.exec(page.url())?.[1] ?? '';

    // The lead, in a browser of their own, opens the same draft from the team's side.
    const lead = await (await browser.newContext()).newPage();
    await signIn(lead, 'active@example.org');
    await lead.goto(`/teams/rocket-team/manage/rules/drafts/${recordId}`);
    await expect(lead.getByText(/is editing this draft \(since/)).toBeVisible();
    await expect(lead.getByRole('textbox', { name: 'Title' })).toHaveAttribute('readonly');

    // The admin leaves: the lead's editor learns it at its next look, and may edit.
    await page.goto('/admin/legal');
    await expect(lead.getByText('Nobody is editing this draft now')).toBeVisible({ timeout: 45_000 });
    await lead.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(lead.getByRole('textbox', { name: 'Title' })).not.toHaveAttribute('readonly');
    await lead.goto('/teams/rocket-team/manage/rules');
    await lead.context().close();

    const draft = page
      .getByRole('region', { name: 'Not in force yet', exact: true })
      .getByRole('listitem')
      .filter({ hasText: 'Uploaded' })
      .filter({ hasText: 'Rocket' });
    await page.reload();
    await draft.first().getByRole('button', { name: 'Delete' }).click();
    await draft.first().getByRole('button', { name: 'Yes, delete' }).click();
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
