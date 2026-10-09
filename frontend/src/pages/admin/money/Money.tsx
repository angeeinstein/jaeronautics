/**
 * Money, the association's side (docs/frontend-structure.md, 4): what each
 * team is owed and what was passed on, each team opening its own page; then
 * the money on Stripe for a period, fetched only when asked, as it pages
 * through Stripe. The period is in the address (?check=1&since=&until=), so
 * a check can be linked to. Data: GET /api/v1/admin/money and
 * /api/v1/admin/money/overview (aeronautics_members/api/admin_money.py).
 */
import { Button, Group, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { type SyntheticEvent, useState } from 'react';
import { useSearchParams } from 'react-router';

import { AppLink } from '../../../app/AppLink';
import { DayInput } from '../../../components/DayInput';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { TeamMark } from '../../../components/TeamMark';
import { formatDate, formatEuros } from '../../../lib/format';
import { EVERY_MINUTE, useLiveRefresh } from '../../../lib/live';
import classes from './Money.module.css';
import { type MoneyOut, moneyQuery } from './shared';
import { FeeSettings } from './FeeSettings';
import { Overview } from './Overview';

function TeamsTable({ money }: { money: MoneyOut }) {
  if (!money.teams.length) {
    return (
      <div className={classes.empty}>
        <EmptyState>No team charges a fee yet.</EmptyState>
      </div>
    );
  }
  return (
    <Table.ScrollContainer minWidth={760} type="native">
      <Table verticalSpacing="sm" highlightOnHover aria-label="What the teams are owed">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Team</Table.Th>
            <Table.Th scope="col" className={classes.amount}>
              Earned
            </Table.Th>
            <Table.Th scope="col" className={classes.amount}>
              Transferred
            </Table.Th>
            <Table.Th scope="col" className={classes.amount}>
              Open
            </Table.Th>
            <Table.Th scope="col">Last transfer</Table.Th>
            <Table.Th scope="col">Account</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {money.teams.map((row) => (
            <Table.Tr key={row.team.slug}>
              <Table.Td>
                <Group gap="sm" wrap="nowrap">
                  <TeamMark name={row.team.name} logoUrl={row.team.logo_url} />
                  <AppLink to={`/admin/money/${row.team.slug}`} className={classes.name}>
                    {row.team.name}
                  </AppLink>
                  {row.team.status === 'archived' ? <Pill tone="neutral">Archived</Pill> : null}
                </Group>
              </Table.Td>
              <Table.Td className={`${classes.amount} ja-figures`}>{formatEuros(row.earned)}</Table.Td>
              <Table.Td className={`${classes.amount} ja-figures`}>{formatEuros(row.paid_out)}</Table.Td>
              <Table.Td className={`${classes.amount} ${classes.open} ja-figures`}>
                {formatEuros(row.open)}
              </Table.Td>
              <Table.Td>{row.last_transfer_on ? formatDate(row.last_transfer_on) : '–'}</Table.Td>
              <Table.Td>
                {row.account ? (
                  <span className="ja-figures">{row.account}</span>
                ) : (
                  <Text span size="sm" c="var(--ja-warning)">
                    Missing
                  </Text>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

/** The period to check: two days and the usual choices. */
function PeriodForm({
  today,
  start,
  since,
  until,
  onCheck,
}: {
  today: string;
  /** The day the portal took over: no check reaches further back. */
  start: string | null;
  since: string;
  until: string;
  onCheck: (since: string, until: string) => void;
}) {
  const [from, setFrom] = useState(since);
  const [to, setTo] = useState(until || today);
  const year = Number(today.slice(0, 4));
  // Only the years the portal has records of.
  const choices = (
    [
      ['Since the start', '', today],
      [String(year), `${String(year)}-01-01`, today],
      [String(year - 1), `${String(year - 1)}-01-01`, `${String(year - 1)}-12-31`],
    ] as [label: string, from: string, to: string][]
  ).filter(([, , choiceTo]) => !start || choiceTo >= start);

  function submit(event: SyntheticEvent) {
    event.preventDefault();
    onCheck(from, to);
  }

  return (
    <Stack gap="xs">
      <form onSubmit={submit}>
        <Group gap="sm" align="flex-end">
          <DayInput
            label="From"
            minDate={start ?? undefined}
            maxDate={today}
            clearable
            value={from}
            onChange={setFrom}
            w={160}
          />
          <DayInput
            label="Until"
            minDate={start ?? undefined}
            maxDate={today}
            value={to}
            onChange={setTo}
            w={160}
          />
          <Button type="submit">Check against Stripe</Button>
        </Group>
      </form>
      <Group gap="md">
        {choices.map(([label, choiceFrom, choiceTo]) => (
          <Button
            key={label}
            variant="subtle"
            size="compact-sm"
            onClick={() => {
              setFrom(choiceFrom);
              setTo(choiceTo);
              onCheck(choiceFrom, choiceTo);
            }}
          >
            {label}
          </Button>
        ))}
        <Text size="sm" c="dimmed">
          {start
            ? `Leave “From” empty for everything since ${formatDate(start)}, when the portal took over.`
            : 'Leave “From” empty for everything from the start.'}
        </Text>
      </Group>
    </Stack>
  );
}

export function Money() {
  const money = useQuery(moneyQuery);
  // Not the check against Stripe (Overview.tsx): that one is asked for.
  useLiveRefresh(moneyQuery.queryKey, EVERY_MINUTE);
  const [params, setParams] = useSearchParams();
  const checking = params.get('check') === '1';
  const since = params.get('since') ?? '';
  const until = params.get('until') ?? '';

  function check(from: string, to: string) {
    const next = new URLSearchParams({ check: '1' });
    if (from) next.set('since', from);
    if (to) next.set('until', to);
    setParams(next);
  }

  return (
    <>
      <PageHeader
        title="Money"
        description="What each team is owed, and the association's money on Stripe."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Money' }]}
      />
      {money.isPending ? (
        <LoadingState />
      ) : money.isError ? (
        <ErrorState error={money.error} onRetry={() => void money.refetch()} />
      ) : (
        <Stack gap="lg">
          <Panel
            title="Teams"
            flush
            actions={
              <Text size="sm" c="dimmed">
                Open in total: <span className="ja-figures">{formatEuros(money.data.total_open)}</span>
              </Text>
            }
          >
            <Text size="sm" c="dimmed" className={classes.note}>
              What teams are owed: what their members paid, less refunds, and what they sold for credit; less
              Stripe&apos;s fees where they carry them (below).
            </Text>
            <TeamsTable money={money.data} />
          </Panel>
          <FeeSettings />
          <Panel title="On Stripe">
            <Stack gap="md">
              <PeriodForm
                today={money.data.today}
                start={money.data.records_since ?? null}
                since={since}
                until={until}
                onCheck={check}
              />
              {checking ? (
                <Overview since={since || null} until={until || null} />
              ) : (
                <Text size="sm" c="dimmed">
                  The totals come from Stripe, where all the association&apos;s payments land: the membership,
                  the teams, and the webshop and events too. They are fetched when you ask, together with a
                  check of every payment against the portal&apos;s records.
                </Text>
              )}
            </Stack>
          </Panel>
        </Stack>
      )}
    </>
  );
}
