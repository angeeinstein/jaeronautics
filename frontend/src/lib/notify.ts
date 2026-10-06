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
