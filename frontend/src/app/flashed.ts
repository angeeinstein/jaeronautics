/**
 * What a Flask route said before sending the browser here -- "This link has
 * expired", "Please confirm your email address first" -- shown once, at the
 * bottom like any other message. Asked for once when the app starts
 * (GET /api/v1/messages hands each out only once).
 */
import { notifications } from '@mantine/notifications';

import { api, call } from '../api/client';

const COLOURS = { info: 'brand', success: 'green', warning: 'yellow', danger: 'red' } as const;

export async function showFlashedMessages(): Promise<void> {
  try {
    const { messages } = await call(api.GET('/api/v1/messages'));
    for (const { tone, text } of messages) {
      notifications.show({
        message: text,
        color: COLOURS[tone],
        // Something to act on stays until it is closed.
        autoClose: tone === 'warning' || tone === 'danger' ? false : 8000,
      });
    }
  } catch {
    // Nothing to say, or the portal unreachable -- the page says so itself.
  }
}
