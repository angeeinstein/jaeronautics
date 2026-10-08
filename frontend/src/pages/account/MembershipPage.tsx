/**
 * My Account › Membership and billing: the membership's state and dates, and
 * the ways to Stripe -- the billing page (payment method, invoices,
 * cancelling), a payment not finished, rejoining after it ended. While a
 * payment just made is being confirmed, the page asks again every few seconds.
 * Data: GET /api/v1/account.
 */
import { PageHeader } from '../../components/PageHeader';
import { ErrorState, LoadingState } from '../../components/States';
import { NoMembership } from './Account';
import { MembershipCard } from './Membership';
import { useAccount } from './shared';

export function MembershipPage() {
  const account = useAccount();
  return (
    <>
      <PageHeader
        title="Membership and billing"
        description="Your membership, what it costs, and paying for it."
        crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Membership and billing' }]}
      />
      {account.isPending ? (
        <LoadingState />
      ) : account.isError ? (
        <ErrorState error={account.error} onRetry={() => void account.refetch()} />
      ) : account.data.member ? (
        <MembershipCard membership={account.data.member.membership} />
      ) : (
        <NoMembership />
      )}
    </>
  );
}
