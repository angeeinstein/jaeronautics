/**
 * One's credit, as the overview's tile and the Credit page both read it
 * (GET /api/v1/account/credit), and how its amounts are written.
 */
import { api, call, type Schemas } from '../../api/client';
import { formatEuros } from '../../lib/format';

export type MyCredit = Schemas['MyCreditOut'];
export type CreditEntry = Schemas['CreditEntryOut'];

export const creditKey = ['account', 'credit'] as const;

export const creditQuery = {
  queryKey: creditKey,
  queryFn: () => call(api.GET('/api/v1/account/credit')),
};

/** "+€15.00" for money in, "−€1.20" for money out. */
export function signedEuros(cents: number): string {
  return `${cents > 0 ? '+' : cents < 0 ? '−' : ''}${formatEuros(Math.abs(cents))}`;
}
