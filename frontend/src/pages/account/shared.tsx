/**
 * What the My Account pages share: the page's data (GET /api/v1/account,
 * aeronautics_members/api/account.py), the choices of the member forms, and
 * one way to take a change's answer -- the page as it is now, and the
 * server's messages about it.
 */
import { Alert, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../api/client';
import { notifyMessage } from '../../lib/notify';

export type Account = Schemas['MyAccountOut'];
export type MemberAccount = Schemas['MemberAccountOut'];
export type FormOptions = Schemas['FormOptionsOut'];

export const accountKey = ['account'] as const;

export const accountQuery = {
  queryKey: accountKey,
  queryFn: () => call(api.GET('/api/v1/account')),
};

export const formOptionsQuery = {
  queryKey: ['forms', 'options'] as const,
  queryFn: () => call(api.GET('/api/v1/forms/options')),
  staleTime: Infinity,
};

/**
 * The account, kept current: asked again every few seconds while a payment
 * just made is being confirmed, and every half minute while an address waits
 * to be confirmed -- confirmed in another tab or on the phone, the page says
 * so by itself.
 */
export function useAccount() {
  return useQuery({
    ...accountQuery,
    refetchInterval: (query) =>
      query.state.data?.member?.membership.activating ? 3000 : query.state.data?.to_confirm ? 30_000 : false,
  });
}

export function useFormOptions() {
  return useQuery(formOptionsQuery);
}

/** A change's answer: the page as it is now, and what the server says about it. */
export function useTakeSaved() {
  const client = useQueryClient();
  return (saved: Schemas['AccountSavedOut']) => {
    client.setQueryData(accountKey, saved.account);
    saved.messages.forEach(notifyMessage);
  };
}

/** Off to Stripe's page (or the thank-you page): the browser leaves the app. */
export function goTo(url: string) {
  window.location.assign(url);
}

const NOTE_COLOURS = {
  plain: 'gray',
  info: 'brand',
  success: 'green',
  warning: 'amber',
  danger: 'red',
} as const;

/** A sentence the server worked out about where things stand. */
export function Note({ note }: { note: Schemas['NoteOut'] }) {
  return (
    <Alert color={NOTE_COLOURS[note.tone]} variant="light" role="status">
      <Text size="sm">{note.text}</Text>
    </Alert>
  );
}
