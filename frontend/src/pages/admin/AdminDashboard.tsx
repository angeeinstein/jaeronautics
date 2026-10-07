/**
 * The admin dashboard: what needs doing, then how the membership stands.
 * The first is a list of tasks that is simply absent when there are none --
 * one all-clear line instead; the second is four figures, each with the one
 * number that explains it. Then the latest log entries. Data:
 * GET /api/v1/admin/dashboard (aeronautics_members/api/admin_dashboard.py).
 */
import { Button, Group, SimpleGrid, Stack, Text, UnstyledButton } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { api, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { AllClear } from '../../components/AllClear';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { StatTile } from '../../components/StatTile';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { formatDateTime, formatEuros, plural, titleFromCode } from '../../lib/format';
import classes from './AdminDashboard.module.css';
import { EVERY_30_SECONDS, useLiveRefresh } from '../../lib/live';

type Dashboard = Schemas['DashboardOut'];
type Attention = Dashboard['attention'];

export const dashboardQuery = {
  queryKey: ['admin', 'dashboard'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/dashboard')),
};

interface Task {
  key: string;
  count: number;
  title: string;
  detail: string;
  to: string;
  action: string;
  primary: boolean;
}

/** What is waiting, as one task per kind -- only kinds with something waiting. */
export function tasksOf(attention: Attention): Task[] {
  const tasks: Task[] = [];
  const {
    name_changes: changes,
    pictures,
    sync_problems: sync,
    health_problems: health,
    transfers,
  } = attention;
  if (changes?.count) {
    tasks.push({
      key: 'name_changes',
      count: changes.count,
      title: plural(changes.count, 'Change request to review', 'Change requests to review'),
      detail: `${changes.count > 1 ? 'Oldest: ' : ''}${changes.summary ?? ''}`,
      to: '/admin/reviews#review-queue',
      action: 'Review',
      primary: true,
    });
  }
  if (pictures?.count) {
    tasks.push({
      key: 'pictures',
      count: pictures.count,
      title: plural(pictures.count, 'Profile picture to review', 'Profile pictures to review'),
      detail: `${pictures.count > 1 ? 'Oldest: ' : ''}${pictures.summary ?? ''}${
        pictures.at ? `, uploaded ${formatDateTime(pictures.at)}` : ''
      }`,
      to: '/admin/reviews#review-queue',
      action: 'Review',
      primary: true,
    });
  }
  if (sync?.count) {
    tasks.push({
      key: 'sync_problems',
      count: sync.count,
      title: plural(sync.count, 'Forum sync problem', 'Forum sync problems'),
      detail: `${sync.count > 1 ? 'Latest: ' : ''}${sync.summary ?? 'An account'} could not be updated on the forum`,
      to: '/admin/reviews#sync-problems',
      action: 'Show',
      primary: false,
    });
  }
  if (health?.length) {
    tasks.push({
      key: 'health',
      count: health.length,
      title: plural(health.length, 'System health problem', 'System health problems'),
      detail: `${health[0] ?? ''}${health.length > 1 ? ` And ${health.length - 1} more.` : ''}`,
      to: '/admin/settings/health',
      action: 'Open health',
      primary: false,
    });
  }
  if (transfers?.teams) {
    tasks.push({
      key: 'transfers',
      count: transfers.teams,
      title: plural(transfers.teams, 'Team to transfer money to', 'Teams to transfer money to'),
      detail: `${formatEuros(transfers.open)} open${
        transfers.without_account
          ? `; ${plural(transfers.without_account, '1 team has', `${String(transfers.without_account)} teams have`)} no bank details yet`
          : ''
      }`,
      to: '/admin/money',
      action: 'Open Money',
      primary: false,
    });
  }
  return tasks;
}

/** The all-clear line, naming what the person would otherwise have seen. */
function allClearDetail(attention: Attention): string {
  const clear: string[] = [];
  if (attention.name_changes !== null || attention.pictures !== null) clear.push('no reviews waiting');
  if (attention.sync_problems !== null) clear.push('no forum problems');
  if (attention.health_problems !== null) clear.push('no system problems');
  if (attention.transfers !== null) clear.push('nothing to transfer to the teams');
  const sentence = clear.join(', ');
  return `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}.`;
}

function NeedsAttention({ attention }: { attention: Attention }) {
  const tasks = tasksOf(attention);
  const seesAnyKind = [
    attention.name_changes,
    attention.pictures,
    attention.sync_problems,
    attention.health_problems,
    attention.transfers,
  ].some((kind) => kind !== null);
  if (!tasks.length) {
    if (!seesAnyKind) return null;
    return <AllClear title="Nothing needs your attention" detail={allClearDetail(attention)} />;
  }
  return (
    <Panel title="Needs your attention" flush>
      {tasks.map((task) => (
        <Group key={task.key} className={classes.task} gap="md">
          <Text className={`${classes.count} ja-figures`}>{task.count}</Text>
          <Stack gap={2} className={classes.taskText}>
            <Text fw={600}>{task.title}</Text>
            <Text size="sm" c="dimmed">
              {task.detail}
            </Text>
          </Stack>
          <Button component={AppLink} to={task.to} size="sm" variant={task.primary ? 'filled' : 'default'}>
            {task.action}
          </Button>
        </Group>
      ))}
    </Panel>
  );
}

function RecentActivity({ entries }: { entries: Dashboard['recent_activity'] }) {
  if (entries === null) return null;
  return (
    <Panel
      title="Recent activity"
      flush
      actions={
        <Button component={AppLink} to="/admin/logs" size="xs" variant="default">
          All logs
        </Button>
      }
    >
      {entries.length ? (
        entries.map((entry) => (
          <UnstyledButton key={entry.id} component={AppLink} to="/admin/logs" className={classes.entry}>
            <Text fw={600} size="sm">
              {titleFromCode(entry.category)} / {titleFromCode(entry.event_type)}
            </Text>
            <Text size="sm" c="dimmed">
              {entry.target ?? '–'} · {formatDateTime(entry.at)}
            </Text>
          </UnstyledButton>
        ))
      ) : (
        <div className={classes.empty}>
          <EmptyState>Nothing has been recorded yet.</EmptyState>
        </div>
      )}
    </Panel>
  );
}

export function AdminDashboard() {
  const dashboard = useQuery(dashboardQuery);
  useLiveRefresh(dashboardQuery.queryKey, EVERY_30_SECONDS);

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={
          dashboard.data?.figures.length === 0
            ? 'What needs doing.'
            : 'What needs doing, and how the membership stands.'
        }
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Dashboard' }]}
      />
      {dashboard.isPending ? (
        <LoadingState />
      ) : dashboard.isError ? (
        <ErrorState error={dashboard.error} onRetry={() => void dashboard.refetch()} />
      ) : (
        <Stack gap="lg">
          <NeedsAttention attention={dashboard.data.attention} />
          {dashboard.data.figures.length ? (
            <SimpleGrid cols={{ base: 1, sm: 2, lg: dashboard.data.figures.length }} spacing="md">
              {dashboard.data.figures.map((figure) => (
                <StatTile
                  key={figure.key}
                  value={figure.value}
                  label={figure.label}
                  note={figure.note}
                  to={figure.link_url ?? undefined}
                />
              ))}
            </SimpleGrid>
          ) : null}
          <RecentActivity entries={dashboard.data.recent_activity} />
        </Stack>
      )}
    </>
  );
}
