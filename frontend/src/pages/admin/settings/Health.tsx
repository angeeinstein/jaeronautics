/**
 * Settings -> System health: whether the installation is well -- problems
 * first, then the figures behind them -- and what can be done about it: the
 * background tasks that gave up sent once more, an email that could not be
 * delivered retried or dismissed. Data: GET /api/v1/admin/settings/health and
 * its actions (aeronautics_members/api/admin_system.py).
 *
 * *Staying current* (docs/automatic-updates-plan.md, part 4): the portal's
 * automatic updates and the updates waiting for a person, Ubuntu's own updates
 * and support, the Node.js that builds the front end. What is stuck is among
 * the report's warnings, and emailed once to whoever may install updates.
 */
import {
  Alert,
  Anchor,
  Button,
  Code,
  Group,
  SimpleGrid,
  Stack,
  Table,
  Text,
  VisuallyHidden,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { IconClock, IconId, IconMailOff, IconRefreshDot, IconStethoscope } from '@tabler/icons-react';

import { api, call, type Schemas } from '../../../api/client';
import { can, useMe } from '../../../api/session';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { type Detail, Details } from '../../../components/Details';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { formatDate, formatDateTime, plural } from '../../../lib/format';
import { notifyDone, notifyFailed, notifyNote } from '../../../lib/notify';
import { SettingsPage } from './shared';

type Health = Schemas['HealthOut'];

const healthQuery = {
  queryKey: ['admin', 'settings', 'health'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/health')),
};

function useRefresh() {
  const client = useQueryClient();
  // The dashboard counts the same problems.
  return () => client.invalidateQueries({ queryKey: ['admin'] });
}

function Report({ health }: { health: Health }) {
  const schema = health.database_schema;
  return (
    <Panel
      icon={IconStethoscope}
      title="Report"
      actions={
        health.healthy ? <Pill tone="active">Healthy</Pill> : <Pill tone="failed">Needs attention</Pill>
      }
    >
      <Stack gap="sm">
        {health.problems.map((problem) => (
          <Alert key={problem} color="red" variant="light" role="alert">
            {problem}
          </Alert>
        ))}
        {health.warnings.map((warning) => (
          <Alert key={warning} color="amber" variant="light">
            {warning}
          </Alert>
        ))}
        {health.healthy && !health.warnings.length ? (
          <Alert color="green" variant="light">
            Nothing needs attention right now.
          </Alert>
        ) : null}
        {health.pages?.note ? (
          <Text size="sm" c="dimmed">
            {health.pages.note}
          </Text>
        ) : null}
        <Text size="sm" c="dimmed">
          Database structure: <Code>{schema.applied ?? 'unknown'}</Code>
          {schema.up_to_date ? (
            ', up to date.'
          ) : (
            <>
              , expected <Code>{schema.expected ?? 'unknown'}</Code>.
            </>
          )}
        </Text>
      </Stack>
    </Panel>
  );
}

function Membership({ health }: { health: Health }) {
  const figures = health.membership;
  return (
    <Panel icon={IconId} title="Membership">
      <Details
        items={[
          ['Members', figures.members],
          ['Currently covered', figures.currently_covered],
          ['Coverage records', figures.coverage_periods],
          ...(figures.revoked_periods ? [['Revoked records', figures.revoked_periods] satisfies Detail] : []),
          // Not a problem: their payment records are kept on purpose.
          ...(figures.erased_members ? [['Erased accounts', figures.erased_members] satisfies Detail] : []),
        ]}
      />
    </Panel>
  );
}

function BackgroundWork({ health }: { health: Health }) {
  const refresh = useRefresh();
  const queues = health.queues;
  const retry = useMutation({
    mutationFn: () => call(api.POST('/api/v1/admin/settings/health/forum-tasks/retry')),
    onSuccess: async ({ count }) => {
      if (count)
        notifyDone(
          `${String(count)} ${plural(count, 'task', 'tasks')} will be tried again within a few minutes.`,
        );
      else notifyNote('No background tasks are waiting to be retried.');
      await refresh();
    },
    onError: notifyFailed,
  });
  return (
    <Panel icon={IconClock} title="Background work">
      <Details
        items={[
          ['Background tasks queued', queues.external_work_pending],
          [
            'Background tasks failed',
            queues.external_work_failed ? (
              <Group gap="sm">
                {queues.external_work_failed}
                {/* They gave up after hours of failing; once the forum is back, this sends them again. */}
                <Button
                  size="compact-xs"
                  variant="default"
                  loading={retry.isPending}
                  onClick={() => {
                    retry.mutate();
                  }}
                >
                  Retry
                </Button>
              </Group>
            ) : (
              0
            ),
          ],
          ['Stripe events handled', queues.webhook_events_completed],
          [
            'Emails queued',
            queues.emails_overdue ? (
              <Group gap="sm">
                {queues.emails_pending}
                <Pill tone="failed">{`${String(queues.emails_overdue)} overdue`}</Pill>
              </Group>
            ) : (
              queues.emails_pending
            ),
          ],
        ]}
      />
    </Panel>
  );
}

function UndeliveredRow({ email, mayResolve }: { email: Schemas['UndeliveredOut']; mayResolve: boolean }) {
  const refresh = useRefresh();
  const resolve = useMutation({
    mutationFn: (action: 'retry' | 'dismiss') =>
      call(
        action === 'retry'
          ? api.POST('/api/v1/admin/settings/health/undelivered/{job_id}/retry', {
              params: { path: { job_id: email.id } },
            })
          : api.POST('/api/v1/admin/settings/health/undelivered/{job_id}/dismiss', {
              params: { path: { job_id: email.id } },
            }),
      ),
    onSuccess: async (_result, action) => {
      notifyDone(
        action === 'retry'
          ? `Queued for another try to ${email.recipient ?? 'the recipient'}.`
          : `Dismissed the email to ${email.recipient ?? 'the recipient'}.`,
      );
      await refresh();
    },
    onError: async (error) => {
      notifyFailed(error);
      await refresh();
    },
  });
  return (
    <Table.Tr>
      <Table.Td>{email.recipient ?? '–'}</Table.Td>
      <Table.Td>{email.kind}</Table.Td>
      <Table.Td>{email.last_tried_at ? formatDateTime(email.last_tried_at) : '–'}</Table.Td>
      <Table.Td c="dimmed" maw={320}>
        <Text size="sm" lineClamp={2}>
          {email.error ?? '–'}
        </Text>
      </Table.Td>
      {mayResolve ? (
        <Table.Td>
          <Group gap="xs" justify="flex-end" wrap="nowrap">
            <Button
              size="xs"
              variant="default"
              loading={resolve.isPending && resolve.variables === 'retry'}
              onClick={() => {
                resolve.mutate('retry');
              }}
            >
              Retry
            </Button>
            <ConfirmButton
              size="xs"
              confirmLabel="Yes, dismiss"
              loading={resolve.isPending && resolve.variables === 'dismiss'}
              onConfirm={() => {
                resolve.mutate('dismiss');
              }}
            >
              Dismiss
            </ConfirmButton>
          </Group>
        </Table.Td>
      ) : null}
    </Table.Tr>
  );
}

function Undelivered({ emails }: { emails: Schemas['UndeliveredOut'][] }) {
  const mayResolve = can(useMe().data, 'notifications.manage');
  return (
    <Panel icon={IconMailOff} title="Emails that could not be delivered" flush>
      <Text size="sm" c="dimmed" px="md" pt="md">
        Retrying sends to the address on the member's profile now, so correct a typo there first. Dismissing
        keeps the record and stops reporting it.
      </Text>
      <Table.ScrollContainer minWidth={720} type="native">
        <Table verticalSpacing="sm" aria-label="Undelivered emails">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Recipient</Table.Th>
              <Table.Th scope="col">Type</Table.Th>
              <Table.Th scope="col">Last tried</Table.Th>
              <Table.Th scope="col">Error</Table.Th>
              {mayResolve ? (
                <Table.Th scope="col">
                  <VisuallyHidden>Actions</VisuallyHidden>
                </Table.Th>
              ) : null}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {emails.map((email) => (
              <UndeliveredRow key={email.id} email={email} mayResolve={mayResolve} />
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Panel>
  );
}

function Waiting({ waiting }: { waiting: Schemas['CurrentOut']['waiting'] }) {
  if (waiting === null) return <Text>Could not be checked</Text>;
  if (!waiting.length) return <Text>None</Text>;
  return (
    <Stack gap={4}>
      {waiting.map((pull) => (
        <Text key={pull.number}>
          <Anchor href={pull.url ?? undefined} target="_blank" rel="noreferrer">
            #{pull.number}
          </Anchor>{' '}
          {pull.title} ({pull.days} {plural(pull.days, 'day', 'days')})
        </Text>
      ))}
    </Stack>
  );
}

function StayingCurrent({ current }: { current: Schemas['CurrentOut'] }) {
  const { portal, ubuntu, node } = current;
  const items: Detail[] = [
    [
      'Automatic updates',
      portal.automatic
        ? `On${portal.last_check_at ? `, last checked ${formatDateTime(portal.last_check_at)}` : ''}`
        : 'Off',
    ],
    ...(portal.newest
      ? [
          [
            'Newest version',
            <Group key="newest" gap="xs">
              <Code>{portal.newest}</Code>
              {portal.newest_failed ? <Pill tone="failed">Checks failed</Pill> : null}
            </Group>,
          ] satisfies Detail,
        ]
      : []),
    ['Waiting for a person', <Waiting key="waiting" waiting={current.waiting} />],
  ];
  if (ubuntu) {
    items.push(
      [
        ubuntu.name,
        ubuntu.supported_until ? `Supported until ${formatDate(ubuntu.supported_until)}` : 'Support unknown',
      ],
      [
        'Security updates',
        ubuntu.updates_installed_at
          ? `Installed ${formatDateTime(ubuntu.updates_installed_at)}`
          : 'Not set up',
      ],
      [
        'Restart',
        ubuntu.restart_pending_since
          ? `Needed since ${formatDateTime(ubuntu.restart_pending_since)}${
              ubuntu.restart_packages.length ? ` (${ubuntu.restart_packages.join(', ')})` : ''
            }`
          : 'Not needed',
      ],
    );
  }
  if (node) {
    items.push([
      'Node.js (builds the front end)',
      node.supported_until ? `${node.major}, fixes until ${formatDate(node.supported_until)}` : node.major,
    ]);
  }
  return (
    <Panel icon={IconRefreshDot} title="Staying current">
      <Stack gap="md">
        <Details items={items} />
        {current.guide_url ? (
          <Anchor href={current.guide_url} target="_blank" rel="noreferrer" size="sm">
            Moving to the next Ubuntu release, step by step
          </Anchor>
        ) : null}
      </Stack>
    </Panel>
  );
}

export function Health() {
  return (
    <SettingsPage
      title="System health"
      description="Whether the installation is well, and what to do when it is not."
      query={healthQuery}
    >
      {(health) => (
        <Stack gap="lg">
          <Report health={health} />
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
            <Membership health={health} />
            <BackgroundWork health={health} />
          </SimpleGrid>
          <StayingCurrent current={health.current} />
          {health.undelivered.length ? <Undelivered emails={health.undelivered} /> : null}
        </Stack>
      )}
    </SettingsPage>
  );
}
