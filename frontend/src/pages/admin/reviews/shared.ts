/**
 * The Reviews page's data, and the one way its decisions are sent: each ends
 * by reloading the queue -- and the counts in the sidebar and on the
 * dashboard, which are the same numbers -- and says how it went.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../../api/client';
import { notifyFailed } from '../../../lib/notify';

export type Reviews = Schemas['ReviewsOut'];
export type QueueItem = Schemas['QueueItem'];

export const reviewsQuery = {
  queryKey: ['admin', 'reviews'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/reviews')),
};

export function historyQuery(page: number) {
  return {
    queryKey: ['admin', 'reviews', 'history', page] as const,
    queryFn: () => call(api.GET('/api/v1/admin/reviews/history', { params: { query: { page } } })),
  };
}

export function useDecision<Input, Output>(
  run: (input: Input) => Promise<Output>,
  done: (output: Output) => void,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async (output) => {
      done(output);
      await Promise.all([
        client.invalidateQueries({ queryKey: ['admin', 'reviews'] }),
        client.invalidateQueries({ queryKey: ['admin', 'dashboard'] }),
        client.invalidateQueries({ queryKey: ['me'] }),
      ]);
    },
    onError: (error) => {
      notifyFailed(error);
    },
  });
}
