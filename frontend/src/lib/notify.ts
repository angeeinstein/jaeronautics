/**
 * The short message at the bottom after an action (docs/frontend-structure.md,
 * 7: "Saved", "Approved") -- and the same place for what went wrong, with the
 * server's own words for it.
 */
import { notifications } from '@mantine/notifications';

import { ApiError } from '../api/client';

export function notifyDone(message: string) {
  notifications.show({ message, color: 'green' });
}

export function notifyNote(message: string) {
  notifications.show({ message, color: 'brand' });
}

export function messageOf(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return 'Something went wrong. Please try again.';
}

export function notifyFailed(error: unknown) {
  notifications.show({ title: 'That did not work', message: messageOf(error), color: 'red' });
}

const TONE_COLOURS = { info: 'brand', success: 'green', warning: 'yellow' } as const;

/** A message from the server in its own tone ("Saved.", "Please confirm …"). */
export function notifyMessage({ tone, text }: { tone: keyof typeof TONE_COLOURS; text: string }) {
  notifications.show({
    message: text,
    color: TONE_COLOURS[tone],
    // Something to act on stays until it is closed.
    autoClose: tone === 'warning' ? false : undefined,
  });
}
