/**
 * The teams pages' data, and the one way their changes are sent: each ends by
 * reloading the teams (and the signed-in person, whose sidebar names teams by
 * their label), and says how it went.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../../api/client';
import { notifyFailed } from '../../../lib/notify';

export type TeamOut = Schemas['TeamOut'];
export type FeeChange = Schemas['FeeChangeOut'];

export const teamsQuery = {
  queryKey: ['admin', 'teams'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/teams')),
};

export function teamQuery(slug: string) {
  return {
    queryKey: ['admin', 'teams', slug] as const,
    queryFn: () => call(api.GET('/api/v1/admin/teams/{slug}', { params: { path: { slug } } })),
  };
}

export function useTeamChange<Input, Output>(
  run: (input: Input) => Promise<Output>,
  { done, onError }: { done: (output: Output) => void; onError?: (error: unknown) => boolean },
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async (output) => {
      done(output);
      await Promise.all([
        client.invalidateQueries({ queryKey: ['admin', 'teams'] }),
        client.invalidateQueries({ queryKey: ['me'] }),
      ]);
    },
    onError: (error) => {
      if (!onError?.(error)) notifyFailed(error);
    },
  });
}

/** What a change of fee did to the members already in, in words; empty when nothing. */
export function feeChangeNotes(change: FeeChange): string[] {
  const notes: string[] = [];
  if (change.moving)
    notes.push(
      `${String(change.moving)} running subscription(s) move to the new price from their next renewal; each member is emailed two weeks before.`,
    );
  if (change.stopping)
    notes.push(
      `${String(change.stopping)} running subscription(s) stop at the end of what is paid; those members stay in for free and are emailed.`,
    );
  if (change.switched)
    notes.push(
      `${String(change.switched)} member(s) keep what they paid for and pay the new way from then on; they are emailed.`,
    );
  if (change.asked_to_pay)
    notes.push(
      `${String(change.asked_to_pay)} member(s) stay free until the next period starts and are emailed to pay by then to stay.`,
    );
  return notes;
}

export const ADMISSION_CHOICES = [
  { value: 'approval', label: 'By approval of the leads' },
  { value: 'open', label: 'Open to every member' },
];

export const PAYMENT_CHOICES = [
  { value: 'none', label: 'Free' },
  { value: 'subscription', label: 'Subscription' },
  { value: 'one_time', label: 'Once per period' },
];
