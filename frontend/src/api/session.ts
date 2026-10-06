/**
 * The signed-in person, loaded once and shared by every part of the page
 * (top bar, sidebar, pages). Refreshed when the window gets focus again, so
 * a count or a permission changed elsewhere shows up without a reload.
 */
import { useQuery } from '@tanstack/react-query';

import { api, call, type Schemas } from './client';

export type Me = Schemas['MeOut'];
export type Permission = Me['permissions'][number];

export const meQuery = {
  queryKey: ['me'] as const,
  queryFn: () => call(api.GET('/api/v1/me')),
  staleTime: 60_000,
};

export function useMe() {
  return useQuery(meQuery);
}

/** Whether the person may do everything in ``permissions``. */
export function can(me: Me | undefined, ...permissions: Permission[]): boolean {
  return me !== undefined && permissions.every((permission) => me.permissions.includes(permission));
}

/** Whether the person may do at least one of ``permissions``. */
export function canAny(me: Me | undefined, ...permissions: Permission[]): boolean {
  return me !== undefined && permissions.some((permission) => me.permissions.includes(permission));
}

/** "Anna Berger", or the forum name or email for an account without a membership. */
export function displayName(me: Me): string {
  const name = [me.first_name, me.last_name].filter(Boolean).join(' ');
  if (name !== '') return name;
  return me.forum_username ?? me.email;
}

/** "AB" for the avatar. */
export function initials(me: Me): string {
  const parts = [me.first_name, me.last_name].filter((part): part is string => Boolean(part));
  const letters = parts.length ? parts.map((part) => part[0]) : [displayName(me)[0]];
  return letters.join('').toUpperCase();
}

/**
 * Log out: the server ends the session (and the forum's, where it can), then
 * the browser goes to the start page.
 */
export async function logOut(): Promise<void> {
  const session = (await fetch('/api/v1/session', { credentials: 'same-origin', cache: 'no-store' }).then(
    (r) => r.json(),
  )) as Schemas['SessionOut'];
  await fetch('/logout', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-CSRFToken': session.csrf_token },
  });
  window.location.assign('/');
}
