/**
 * The Money pages' data, and reading an amount the way the server does
 * (services/team_money.py): "240", "240.00", "240,00" or "1.240,00".
 */
import { api, call, type Schemas } from '../../../api/client';

export type MoneyOut = Schemas['MoneyOut'];
export type OverviewOut = Schemas['OverviewOut'];
export type TeamMoneyOut = Schemas['TeamMoneyOut'];

export const moneyQuery = {
  queryKey: ['admin', 'money'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/money')),
};

export function overviewQuery(since: string | null, until: string | null) {
  return {
    queryKey: ['admin', 'money', 'overview', since, until] as const,
    queryFn: () =>
      call(
        api.GET('/api/v1/admin/money/overview', {
          params: { query: { since: since ?? undefined, until: until ?? undefined } },
        }),
      ),
    // Each check pages through Stripe: asked for, not refreshed behind the reader's back.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  };
}

export function teamMoneyQuery(slug: string) {
  return {
    queryKey: ['admin', 'money', 'team', slug] as const,
    queryFn: () => call(api.GET('/api/v1/admin/money/{slug}', { params: { path: { slug } } })),
  };
}

/** Euros as typed, in cents; ``null`` when it is no amount. */
export function centsOf(text: string): number | null {
  let cleaned = text.trim().replace('€', '').replaceAll(' ', '');
  if (cleaned.includes(',') && cleaned.includes('.')) cleaned = cleaned.replaceAll('.', '');
  cleaned = cleaned.replace(',', '.');
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  return Math.round(Number(cleaned) * 100);
}

/** Cents as the amount field shows them: "240.00". */
export function amountText(cents: number): string {
  return (cents / 100).toFixed(2);
}
