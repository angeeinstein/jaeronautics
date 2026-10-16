/**
 * My Account › Privacy and data: everything held about this person as a file,
 * and deleting the account (YourData.tsx). Stays at the attendance readers are
 * reached from here once attendance is not on for them any more
 * (docs/attendance-plan.md). Data: GET /api/v1/account.
 */
import { Button, Group, Stack, Text } from '@mantine/core';
import { IconClockHour4 } from '@tabler/icons-react';

import { useMe } from '../../api/session';
import { AppLink } from '../../app/AppLink';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { ErrorState, LoadingState } from '../../components/States';
import { useAccount } from './shared';
import { YourDataCard } from './YourData';

/** Kept stays, out of the side menu: attendance is off for them, or they left the team. */
function KeptAttendance() {
  return (
    <Panel icon={IconClockHour4} title="Attendance">
      <Group justify="space-between" gap="sm">
        <Text size="sm">Your stays at the attendance readers.</Text>
        <Button component={AppLink} to="/account/attendance" variant="default">
          Show
        </Button>
      </Group>
    </Panel>
  );
}

export function Data() {
  const account = useAccount();
  const me = useMe().data;
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
        <Stack gap="lg">
          <YourDataCard
            exportUrl={account.data.export_url}
            deletionNote={account.data.member?.deletion_note ?? null}
            mayCancelInstead={account.data.member?.may_cancel_instead ?? false}
          />
          {me?.attendance_area === 'data' ? <KeptAttendance /> : null}
        </Stack>
      )}
    </>
  );
}
