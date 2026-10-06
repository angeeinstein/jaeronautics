/**
 * The latest entries of the log that concern this account, done to it or by
 * it, each with what changed -- folded until asked for.
 */
import { Code, Stack, Text, UnstyledButton } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';

import { AppLink } from '../../../app/AppLink';
import { Panel } from '../../../components/Panel';
import { EmptyState } from '../../../components/States';
import { formatDateTime, titleFromCode } from '../../../lib/format';
import type { Account } from './shared';
import classes from './Account.module.css';

type Entry = Account['recent_activity'][number];

function Payload({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined) return null;
  return (
    <Stack gap={2}>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Code block>{JSON.stringify(value, null, 2)}</Code>
    </Stack>
  );
}

function ActivityEntry({ entry }: { entry: Entry }) {
  const [open, { toggle }] = useDisclosure(false);
  const hasDetails = entry.before != null || entry.after != null || entry.details != null;
  return (
    <div className={classes.entry}>
      <UnstyledButton
        className={classes.entryHead}
        onClick={toggle}
        disabled={!hasDetails}
        aria-expanded={hasDetails ? open : undefined}
      >
        <Text fw={600} size="sm">
          {titleFromCode(entry.category)} / {titleFromCode(entry.event_type)}
        </Text>
        <Text size="sm" c="dimmed">
          {formatDateTime(entry.at)} · {entry.actor ?? 'the system'}
        </Text>
      </UnstyledButton>
      {open ? (
        <Stack gap="xs" mt="xs">
          <Payload label="Before" value={entry.before} />
          <Payload label="After" value={entry.after} />
          <Payload label="Details" value={entry.details} />
        </Stack>
      ) : null}
    </div>
  );
}

export function ActivityTab({ account }: { account: Account }) {
  return (
    <Panel title="Recent activity" flush actions={<AppLink to="/admin/logs">All logs</AppLink>}>
      {account.recent_activity.length ? (
        account.recent_activity.map((entry) => <ActivityEntry key={entry.id} entry={entry} />)
      ) : (
        <div className={classes.empty}>
          <EmptyState>Nothing recorded for this account yet.</EmptyState>
        </div>
      )}
    </Panel>
  );
}
