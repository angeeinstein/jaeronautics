/**
 * Every test signs in through the real login page, and fails if the browser
 * reports anything the security policy blocked or an error in the console --
 * the kind of fault that leaves a page quietly unstyled in production.
 */
import { expect, type Page, test as base } from '@playwright/test';

export const PASSWORD = 'snapshot-password';
export const ADMIN = 'admin@example.org';

interface Watched {
  problems: string[];
  /** What a test expects the console to say -- an error answer it is about. */
  expected: RegExp[];
}

export const test = base.extend<{ watched: Watched }>({
  watched: [
    async ({ page }, use) => {
      const watched: Watched = { problems: [], expected: [] };
      await page.addInitScript(() => {
        document.addEventListener('securitypolicyviolation', (event) => {
          console.error(`CSP violation: ${event.violatedDirective} ${event.blockedURI}`);
        });
      });
      page.on('console', (message) => {
        if (message.type() === 'error') watched.problems.push(message.text());
      });
      page.on('pageerror', (error) => watched.problems.push(error.message));
      await use(watched);
      const unexpected = watched.problems.filter(
        (problem) => !watched.expected.some((pattern) => pattern.test(problem)),
      );
      expect(unexpected, 'console errors or blocked content').toEqual([]);
    },
    { auto: true },
  ],
});

export async function signIn(page: Page, email = ADMIN) {
  await page.goto('/login');
  await page.getByLabel(/email/i).first().fill(email);
  await page
    .getByLabel(/password/i)
    .first()
    .fill(PASSWORD);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login')),
    page.locator('form[action$="/login"] [type=submit]').click(),
  ]);
}

export { expect };
