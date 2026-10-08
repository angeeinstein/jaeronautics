/**
 * My Account › Privacy and data: everything held about this person as a file,
 * and deleting the account (YourData.tsx). Data: GET /api/v1/account.
 */
import { PageHeader } from '../../components/PageHeader';
import { ErrorState, LoadingState } from '../../components/States';
import { useAccount } from './shared';
import { YourDataCard } from './YourData';

export function Data() {
  const account = useAccount();
  return (
    <>
      <PageHeader
        title="Privacy and data"
        description="What we store about you, and deleting your account."
        crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Privacy and data' }]}
      />
      {account.isPending ? (
        <LoadingState />
      ) : account.isError ? (
        <ErrorState error={account.error} onRetry={() => void account.refetch()} />
      ) : (
        <YourDataCard
          exportUrl={account.data.export_url}
          deletionNote={account.data.member?.deletion_note ?? null}
          mayCancelInstead={account.data.member?.may_cancel_instead ?? false}
        />
      )}
    </>
  );
}
