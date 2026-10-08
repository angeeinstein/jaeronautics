/**
 * What the admin's credit pages share: their data (GET /api/v1/admin/credit
 * and /api/v1/admin/credit/<user_id>, aeronautics_members/api/credit.py).
 */
import { api, call, type Schemas } from '../../../api/client';

export type CreditOverview = Schemas['CreditOverviewOut'];
export type CreditHolder = Schemas['CreditHolderOut'];

export { initialsOf } from '../../../lib/format';

export const creditOverviewQuery = {
  queryKey: ['admin', 'credit'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/credit')),
};

export function holderQuery(userId: number) {
  return {
    queryKey: ['admin', 'credit', userId] as const,
    queryFn: () => call(api.GET('/api/v1/admin/credit/{user_id}', { params: { path: { user_id: userId } } })),
  };
}
