/**
 * A membership for somebody signed in without one -- an account made to run
 * the portal, say -- with this login's address (POST /api/v1/account/membership).
 * With a membership already, back to My Account.
 */
import { useQuery } from '@tanstack/react-query';
import { Navigate } from 'react-router';

import { PageHeader } from '../../components/PageHeader';
import { ErrorState, LoadingState } from '../../components/States';
import { MembershipForm } from '../public/MembershipForm';
import { accountQuery } from './shared';

export function CreateMembership() {
  const account = useQuery(accountQuery);
  if (account.data?.member) return <Navigate to="/account" replace />;
  return (
    <>
      <PageHeader
        title="Start your membership"
        crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Start your membership' }]}
        description="This account has no membership yet. Start one with this login."
      />
      {account.isPending ? (
        <LoadingState />
      ) : account.isError ? (
        <ErrorState error={account.error} onRetry={() => void account.refetch()} />
      ) : (
        <MembershipForm door={{ kind: 'profile', email: account.data.email.address }} />
      )}
    </>
  );
}
