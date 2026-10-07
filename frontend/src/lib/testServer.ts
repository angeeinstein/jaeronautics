/**
 * Whether this is the test server (TEST_SERVER in the server's .env): Flask
 * marks the page with ``data-test-server`` (blueprints/app_shell.py), and the
 * app says so on every page -- the red bar, [TEST] in the tab's title.
 */
export function onTestServer(): boolean {
  return document.documentElement.hasAttribute('data-test-server');
}
