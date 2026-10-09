/**
 * My Account › Credit: the balance, topping it up, and every change of it
 * (docs/credit-plan.md). Topping up leaves for Stripe's payment page, which
 * comes back here with ``?topped_up=1``: the page then asks again every two
 * seconds until the top-up is in, and the balance counts up to it.
 *
 * Data: GET /api/v1/account/credit, POST /api/v1/account/credit/top-up
 * (aeronautics_members/api/credit.py).
 */
import { Alert, Anchor, Button, Group, Loader, NumberInput, Stack, Table, Text } from '@mantine/core';
import { IconDownload, IconReceipt } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';

import { api, call } from '../../api/client';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { ErrorState, LoadingState } from '../../components/States';
import { useCountUp } from '../../lib/countUp';
import { formatDateTime, formatEuros } from '../../lib/format';
import { notifyFailed } from '../../lib/notify';
import classes from './Credit.module.css';
import { type CreditEntry, creditQuery, type MyCredit, signedEuros } from './creditData';
import { goTo } from './shared';

/** How long after coming back from Stripe the page keeps asking. */
const WAIT_MS = 2 * 60 * 1000;
/** A top-up this recent when the page opens is the one just paid. */
const RECENT_MS = 10 * 60 * 1000;

/** The balance, large, and how much of the most one may hold it is. */
export function Balance({
  cents,
  maxCents,
  label = 'Your credit',
  note,
}: {
  cents: number;
  maxCents: number;
  label?: string;
  note?: string;
}) {
  const shown = useCountUp(cents);
  const filled = maxCents > 0 ? Math.min(1, Math.max(0, cents / maxCents)) : 0;
  return (
    <section className={classes.balance} aria-labelledby="credit-balance-label">
      <span className={classes.label} id="credit-balance-label">
        {label}
      </span>
      <span className={classes.amount} data-negative={cents < 0 || undefined} aria-live="polite">
        {formatEuros(shown)}
      </span>
      <span className={classes.bar} aria-hidden>
        <span style={{ width: `${String(Math.round(filled * 100))}%` }} />
      </span>
      <span className={classes.muted}>{note ?? `At most ${formatEuros(maxCents)} on your account.`}</span>
    </section>
  );
}

function TopUp({ credit }: { credit: MyCredit }) {
  const { top_up: topUp } = credit;
  const [picked, setPicked] = useState<number | 'other'>(topUp.choices.at(-1) ?? 'other');
  const [other, setOther] = useState<number | ''>('');
  const go = useMutation({
    mutationFn: (amountCents: number) =>
      call(api.POST('/api/v1/account/credit/top-up', { body: { amount_cents: amountCents } })),
    onSuccess: ({ url }) => {
      goTo(url);
    },
    onError: notifyFailed,
  });

  if (topUp.refused) {
    return (
      <Panel title="Top up">
        <Text c="dimmed">{topUp.refused}</Text>
      </Panel>
    );
  }
  const otherCents = other === '' ? null : Math.round(other * 100);
  const otherFits = otherCents !== null && otherCents >= topUp.least_cents && otherCents <= topUp.most_cents;
  const amount = picked === 'other' ? (otherFits ? otherCents : null) : picked;
  return (
    <Panel title="Top up">
      <Stack gap="md">
        <Group gap="xs" role="radiogroup" aria-label="How much">
          {topUp.choices.map((cents) => (
            <Button
              key={cents}
              role="radio"
              aria-checked={picked === cents}
              variant={picked === cents ? 'filled' : 'default'}
              className={classes.choice}
              onClick={() => {
                setPicked(cents);
              }}
            >
              {formatEuros(cents)}
            </Button>
          ))}
          <Button
            role="radio"
            aria-checked={picked === 'other'}
            variant={picked === 'other' ? 'filled' : 'default'}
            className={classes.choice}
            onClick={() => {
              setPicked('other');
            }}
          >
            Other amount
          </Button>
        </Group>
        {picked === 'other' ? (
          <NumberInput
            label="Amount"
            description={`Between ${formatEuros(topUp.least_cents)} and ${formatEuros(topUp.most_cents)}.`}
            inputWrapperOrder={['label', 'input', 'description', 'error']}
            prefix="€"
            decimalScale={2}
            min={topUp.least_cents / 100}
            max={topUp.most_cents / 100}
            allowNegative={false}
            value={other}
            maw={220}
            error={other !== '' && !otherFits ? 'Outside what you can top up.' : undefined}
            onChange={(value) => {
              setOther(typeof value === 'number' ? value : value === '' ? '' : Number(value));
            }}
          />
        ) : null}
        <Group justify="space-between" align="center" gap="md">
          <Text size="sm" c="dimmed">
            Paid securely through Stripe.
          </Text>
          <Button
            disabled={amount === null}
            loading={go.isPending}
            onClick={() => {
              if (amount !== null) go.mutate(amount);
            }}
          >
            {amount !== null ? `Top up ${formatEuros(amount)}` : 'Top up'}
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

export function History({
  entries,
  exportUrl,
}: {
  entries: (CreditEntry & { booked_by?: string | null })[];
  exportUrl?: string;
}) {
  return (
    <Panel
      title="History"
      flush
      actions={
        exportUrl && entries.length ? (
          <Anchor href={exportUrl} size="sm" className={classes.download}>
            <IconDownload size={16} aria-hidden /> Download
          </Anchor>
        ) : null
      }
    >
      {entries.length ? (
        <div className={classes.scroll}>
          <Table verticalSpacing="sm" horizontalSpacing="md" aria-label="History">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col" className={classes.wide}>
                  When
                </Table.Th>
                <Table.Th scope="col">What</Table.Th>
                <Table.Th scope="col" className={classes.right}>
                  Amount
                </Table.Th>
                <Table.Th scope="col" className={`${classes.right} ${classes.wide}`}>
                  Balance
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {entries.map((entry) => (
                <Table.Tr key={entry.id}>
                  <Table.Td className={`${classes.when} ${classes.wide}`}>
                    {formatDateTime(entry.at)}
                  </Table.Td>
                  <Table.Td>
                    <span className={classes.what}>
                      <span>{entry.kind_label}</span>
                      <span className={`${classes.muted} ${classes.narrow}`}>{formatDateTime(entry.at)}</span>
                      {entry.description !== entry.kind_label ? (
                        <span className={classes.muted}>{entry.description}</span>
                      ) : null}
                      {entry.team_name ? <span className={classes.muted}>{entry.team_name}</span> : null}
                      {entry.booked_by ? (
                        <span className={classes.muted}>{`Booked by ${entry.booked_by}`}</span>
                      ) : null}
                      {entry.receipt_url ? (
                        <Anchor href={entry.receipt_url} size="sm" className={classes.receipt}>
                          <IconReceipt size={14} aria-hidden /> Receipt
                        </Anchor>
                      ) : null}
                    </span>
                  </Table.Td>
                  <Table.Td className={classes.money} data-sign={Math.sign(entry.amount_cents)}>
                    {signedEuros(entry.amount_cents)}
                  </Table.Td>
                  <Table.Td className={`${classes.money} ${classes.muted} ${classes.wide}`}>
                    {formatEuros(entry.balance_after_cents)}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
      ) : (
        <Text c="dimmed" p="md">
          Nothing yet. Top-ups and everything paid with your credit are listed here.
        </Text>
      )}
    </Panel>
  );
}

/** A top-up from the last few minutes: the one just paid has arrived. */
function arrived(credit: MyCredit | undefined, since: number): CreditEntry | undefined {
  return credit?.entries.find((entry) => entry.kind === 'top_up' && new Date(entry.at).getTime() >= since);
}

/** Back from Stripe: wait for the top-up, say when it is in, then forget the address's mark. */
function useArrival() {
  const [params, setParams] = useSearchParams();
  const back = params.get('topped_up') !== null;
  const [since] = useState(() => Date.now() - RECENT_MS);
  const [timedOut, setTimedOut] = useState(false);
  useEffect(() => {
    if (!back) return undefined;
    const timer = window.setTimeout(() => {
      setTimedOut(true);
    }, WAIT_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [back]);
  const waiting = back && !timedOut;
  const credit = useQuery({
    ...creditQuery,
    refetchInterval: (query) => {
      if (!waiting || arrived(query.state.data, since)) return false;
      return 2000;
    },
  });
  const found = back ? arrived(credit.data, since) : undefined;
  const settle = () => {
    setParams(
      (current) => {
        current.delete('topped_up');
        return current;
      },
      { replace: true },
    );
  };
  return {
    credit,
    found,
    waiting: waiting && !found,
    late: back && !found && !waiting,
    settle,
    check: () => void credit.refetch(),
  };
}

function Arrival({ arrival }: { arrival: ReturnType<typeof useArrival> }) {
  const { found, waiting, late, settle, check } = arrival;
  if (found) {
    return (
      <Alert
        color="green"
        withCloseButton
        onClose={settle}
        title={`${formatEuros(found.amount_cents)} added`}
      >
        Thank you. Your top-up is on your account.
      </Alert>
    );
  }
  if (waiting) {
    return (
      <Alert color="brand" icon={<Loader size="xs" />} title="Payment received">
        <Group gap="xs" justify="space-between">
          <span>Adding it to your credit. This takes a few seconds.</span>
          <Button size="compact-xs" variant="subtle" onClick={check}>
            Check now
          </Button>
        </Group>
      </Alert>
    );
  }
  if (late) {
    return (
      <Alert color="amber" withCloseButton onClose={settle} title="Not here yet">
        Stripe has not confirmed the payment yet. It is added as soon as it does; nothing more to do.
      </Alert>
    );
  }
  return null;
}

export function Credit() {
  const arrival = useArrival();
  const { credit } = arrival;
  return (
    <>
      <PageHeader
        title="Credit"
        description="Money on your account, for coffee, drinks and more from the association."
        crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Credit' }]}
      />
      {credit.isPending ? (
        <LoadingState />
      ) : credit.isError ? (
        <ErrorState error={credit.error} onRetry={() => void credit.refetch()} />
      ) : (
        <Stack gap="lg">
          <Arrival arrival={arrival} />
          <Balance cents={credit.data.balance_cents} maxCents={credit.data.top_up.max_balance_cents} />
          <TopUp key={credit.data.balance_cents} credit={credit.data} />
          <History entries={credit.data.entries} exportUrl={credit.data.export_url} />
        </Stack>
      )}
    </>
  );
}
