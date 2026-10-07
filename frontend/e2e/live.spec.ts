import { expect, signIn, test } from './fixtures';

test.describe('pages that refresh themselves', () => {
  test('an account that joins appears in the open list, marked', async ({ page, playwright }, testInfo) => {
    await page.clock.install();
    await signIn(page);
    await page.goto('/admin/accounts?sort=created&desc=1');
    const table = page.getByRole('table', { name: 'Accounts' });
    await expect(table).toBeVisible();

    // Somebody else joins meanwhile, in a browser of their own.
    const other = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
    const { csrf_token: token } = (await (await other.get('/api/v1/session')).json()) as {
      csrf_token: string;
    };
    const stamp = `${testInfo.project.name}${String(Date.now())}`;
    const joined = await other.post('/api/v1/signup', {
      headers: { 'X-CSRFToken': token },
      data: {
        salutation: 'Ms',
        title: null,
        first_name: 'Live',
        last_name: `Arrival${stamp}`,
        street: 'Alte Poststraße',
        house_number: '149',
        postal_code: '8020',
        city: 'Graz',
        country: 'Austria',
        phone_private: '+43 316 123456',
        phone_work: null,
        email_private: `live-${stamp}@example.org`,
        email_work: null,
        member_category: 'partner',
        year_group: null,
        password: 'a-good-password',
        payment_method: 'checkout',
        terms_accepted: true,
      },
    });
    expect(joined.ok()).toBe(true);
    await other.dispose();

    await page.clock.fastForward('01:05');

    const row = page
      .getByRole('table', { name: 'Accounts' })
      .getByRole('row', { name: new RegExp(`Arrival${stamp}`) });
    await expect(row).toBeVisible();
    await expect(row).toHaveClass(/ja-arrived/);
  });
});
