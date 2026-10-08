/**
 * The account page's data and the one way its tabs act on it: every action
 * ends by reloading the account (and any list it appears in), and says how
 * it went at the bottom of the screen.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../../api/client';
import { notifyFailed } from '../../../lib/notify';

export type Account = Schemas['AccountOut'];

export function accountQuery(id: number) {
  return {
    queryKey: ['admin', 'accounts', 'detail', id] as const,
    queryFn: () => call(api.GET('/api/v1/admin/accounts/{user_id}', { params: { path: { user_id: id } } })),
  };
}

export function erasureQuery(id: number) {
  return {
    queryKey: ['admin', 'accounts', 'erasure', id] as const,
    queryFn: () =>
      call(api.GET('/api/v1/admin/accounts/{user_id}/erasure', { params: { path: { user_id: id } } })),
  };
}

export function candidatesQuery(id: number, q: string) {
  return {
    queryKey: ['admin', 'accounts', 'old-forum', id, q] as const,
    queryFn: () =>
      call(
        api.GET('/api/v1/admin/accounts/{user_id}/old-forum-candidates', {
          params: { path: { user_id: id }, query: { q } },
        }),
      ),
  };
}

/**
 * An action on the account. ``done`` gets the answer, to say what happened;
 * a refusal is shown as the server worded it, unless ``onError`` takes it
 * (to put a field's message beside the field).
 */
export function useAccountAction<Input, Output>(
  run: (input: Input) => Promise<Output>,
  { done, onError }: { done: (output: Output) => void; onError?: (error: unknown) => boolean },
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async (output) => {
      done(output);
      await client.invalidateQueries({ queryKey: ['admin', 'accounts'] });
      await client.invalidateQueries({ queryKey: ['admin', 'dashboard'] });
    },
    onError: (error) => {
      if (!onError?.(error)) notifyFailed(error);
    },
  });
}

export const path = (id: number) => ({ params: { path: { user_id: id } } });
