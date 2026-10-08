/**
 * One team's money, the association's side (docs/frontend-structure.md, 4):
 * the balance, the transfer -- with a code a banking app scans, filled in with
 * the amount and reference as typed -- where it goes, and every period,
 * transfer and payment. The team's leads see their own on the team's page.
 * Data: GET /api/v1/admin/money/<slug> (aeronautics_members/api/admin_money.py).
 */
import { Alert, Button, Grid, Group, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router';

import { api, ApiError, call } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { DayInput } from '../../../components/DayInput';
import { Details } from '../../../components/Details';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { arrivedClass, useArrivals } from '../../../lib/arrivals';
import { formatDate, formatDayOf, formatEuros } from '../../../lib/format';
import { EVERY_MINUTE, useLiveRefresh } from '../../../lib/live';
import { notifyDone, notifyFailed, notifyNote } from '../../../lib/notify';
import classes from './Money.module.css';
import { amountText, centsOf, type TeamMoneyOut, teamMoneyQuery } from './shared';

/** AT61 1904 3002 3457 3201, as it is read out and typed. */
function grouped(iban: string): string {
  return iban.replace(/(.{4})/g, '$1 ').trim();
}

/** A change that answers with the team's money as it now stands. */
function useMoneyChange<Input>(slug: string, run: (input: Input) => Promise<TeamMoneyOut>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async (money) => {
      client.setQueryData(teamMoneyQuery(slug).queryKey, money);
      await Promise.all([
        client.invalidateQueries({ queryKey: ['admin', 'money'], exact: true }),
        client.invalidateQueries({ queryKey: ['admin', 'dashboard'] }),
      ]);
    },
  });
}

function Balance({ money }: { money: TeamMoneyOut }) {
  const last = money.transfers[0];
  return (
    <Panel title="Balance">
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, xs: 3 }} spacing="md">
          {(
            [
              ['Paid by members', money.earned],
              ['Transferred to the team', money.paid_out],
              ['Open', money.open],
            ] as const
          ).map(([label, cents]) => (
            <div key={label} className={classes.figure}>
              <Text size="sm" c="dimmed">
                {label}
              </Text>
              <Text className={`${classes.figureValue} ja-figures`} fw={label === 'Open' ? 700 : 400}>
                {formatEuros(cents)}
              </Text>
            </div>
          ))}
        </SimpleGrid>
        <Text size="sm" c="dimmed">
          The team gets exactly what its members paid, less refunds. Stripe&apos;s fees are paid by the
          association.
          {last ? ` Last transfer: ${formatDate(last.paid_on)}.` : ''}
        </Text>
      </Stack>
    </Panel>
  );
}

function TransferPanel({ money }: { money: TeamMoneyOut }) {
  const slug = money.team.slug;
  const [amount, setAmount] = useState(amountText(money.open));
  const [paidOn, setPaidOn] = useState(money.today);
  const [reference, setReference] = useState(money.reference);
  const cents = centsOf(amount);
  const [code] = useDebouncedValue({ cents, reference }, 400);
  const record = useMoneyChange(slug, () =>
    call(
      api.POST('/api/v1/admin/money/{slug}/transfers', {
        params: { path: { slug } },
        body: { amount, paid_on: paidOn || null, reference: reference.trim() || null },
      }),
    ),
  );
  const payable = money.bank.iban && money.bank.account_holder;

  if (money.open <= 0) {
    return (
      <Panel title="Transfer to the team">
        <EmptyState>Nothing open.</EmptyState>
      </Panel>
    );
  }
  return (
    <Panel title="Transfer to the team">
      <Grid gap="xl">
        <Grid.Col span={{ base: 12, md: 5 }}>
          {payable ? (
            <Stack gap="xs">
              {code.cents ? (
                <img
                  className={classes.code}
                  src={`/admin/money/${slug}/transfer-code.svg?${new URLSearchParams({
                    amount: String(code.cents),
                    reference: code.reference,
                  }).toString()}`}
                  alt={`Transfer code for ${formatEuros(code.cents)}`}
                />
              ) : null}
              <Text size="sm" c="dimmed">
                Scan with your banking app (GiroCode). It fills in the amount and reference below.
              </Text>
            </Stack>
          ) : (
            <Alert color="amber" variant="light">
              The team has no bank details yet, so there is no code to scan.
            </Alert>
          )}
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack gap="md">
            {money.bank.iban ? (
              <Details
                items={[
                  ['Account holder', money.bank.account_holder],
                  ['IBAN', <span className="ja-figures">{grouped(money.bank.iban)}</span>],
                  ['BIC', money.bank.bic],
                ]}
              />
            ) : null}
            <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="md">
              <TextInput
                label="Amount (€)"
                inputMode="decimal"
                value={amount}
                error={amount && cents === null ? 'Enter the amount in euros, like 240.00.' : null}
                onChange={(event) => {
                  setAmount(event.currentTarget.value);
                }}
              />
              <DayInput label="Sent on" maxDate={money.today} value={paidOn} onChange={setPaidOn} />
            </SimpleGrid>
            <TextInput
              label="Reference"
              maxLength={140}
              value={reference}
              onChange={(event) => {
                setReference(event.currentTarget.value);
              }}
            />
            <Text size="sm" c="dimmed">
              Record it only once the money has really been sent: it is kept in the records and cannot be
              undone here.
            </Text>
            <Group justify="flex-end">
              <ConfirmButton
                color="brand"
                disabled={!cents}
                loading={record.isPending}
                confirmLabel={`Yes, record ${cents ? formatEuros(cents) : ''}`}
                onConfirm={() => {
                  record.mutate(undefined, {
                    onSuccess: () => {
                      notifyDone('Transfer recorded.');
                    },
                    onError: notifyFailed,
                  });
                }}
              >
                Mark as transferred
              </ConfirmButton>
            </Group>
          </Stack>
        </Grid.Col>
      </Grid>
    </Panel>
  );
}

/** Which field a refusal of the bank details is about (services/team_money.py). */
const BANK_FIELDS: Record<string, 'account_holder' | 'iban' | 'bic'> = {
  team_iban_invalid: 'iban',
  team_account_holder_missing: 'account_holder',
  team_bic_invalid: 'bic',
};

function BankPanel({ money }: { money: TeamMoneyOut }) {
  const slug = money.team.slug;
  const [value, setValue] = useState({
    account_holder: money.bank.account_holder ?? '',
    iban: money.bank.iban ?? '',
    bic: money.bank.bic ?? '',
  });
  const [problem, setProblem] = useState<{ field: string; message: string } | null>(null);
  const save = useMoneyChange(slug, () =>
    call(
      api.PUT('/api/v1/admin/money/{slug}/bank', {
        params: { path: { slug } },
        body: {
          account_holder: value.account_holder || null,
          iban: value.iban || null,
          bic: value.bic || null,
        },
      }),
    ),
  );
  const unchanged =
    value.account_holder === (money.bank.account_holder ?? '') &&
    value.iban === (money.bank.iban ?? '') &&
    value.bic === (money.bank.bic ?? '');

  function field(name: 'account_holder' | 'iban' | 'bic') {
    return {
      value: value[name],
      error: problem?.field === name ? problem.message : null,
      autoComplete: 'off',
      spellCheck: false,
      onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
        setValue({ ...value, [name]: event.currentTarget.value });
        setProblem(null);
      },
    };
  }

  return (
    <Panel title="Bank details">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Where the association transfers the team&apos;s money. Every change is logged, and the
          association&apos;s treasurers are told.
        </Text>
        <TextInput label="Account holder" maxLength={70} {...field('account_holder')} />
        <Grid gap="md">
          <Grid.Col span={{ base: 12, sm: 8 }}>
            <TextInput label="IBAN" maxLength={42} {...field('iban')} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, sm: 4 }}>
            <TextInput label="BIC (optional)" maxLength={11} {...field('bic')} />
          </Grid.Col>
        </Grid>
        <Group justify="flex-end">
          <Button
            loading={save.isPending}
            onClick={() => {
              if (unchanged) {
                notifyNote('Nothing changed.');
                return;
              }
              save.mutate(undefined, {
                onSuccess: () => {
                  notifyDone('Bank details saved.');
                },
                onError: (error) => {
                  const name = error instanceof ApiError ? BANK_FIELDS[error.code] : undefined;
                  if (name && error instanceof ApiError) setProblem({ field: name, message: error.message });
                  else notifyFailed(error);
                },
              });
            }}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function Periods({ money }: { money: TeamMoneyOut }) {
  return (
    <Panel title="By period" flush>
      {money.by_period.length ? (
        <Table verticalSpacing="sm" aria-label="By period">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Paid until</Table.Th>
              <Table.Th scope="col" className={classes.amount}>
                Paid by members
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {money.by_period.map((period) => (
              <Table.Tr key={period.paid_until ?? 'none'}>
                <Table.Td>{period.paid_until ? formatDate(period.paid_until) : '–'}</Table.Td>
                <Table.Td className={`${classes.amount} ja-figures`}>{formatEuros(period.earned)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      ) : (
        <div className={classes.empty}>
          <EmptyState>No payments yet.</EmptyState>
        </div>
      )}
    </Panel>
  );
}

function Transfers({ money }: { money: TeamMoneyOut }) {
  return (
    <Panel title="Transfers" flush>
      {money.transfers.length ? (
        <Table.ScrollContainer minWidth={560} type="native">
          <Table verticalSpacing="sm" aria-label="Transfers">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Sent on</Table.Th>
                <Table.Th scope="col" className={classes.amount}>
                  Amount
                </Table.Th>
                <Table.Th scope="col">Reference</Table.Th>
                <Table.Th scope="col">To</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {money.transfers.map((transfer) => (
                <Table.Tr key={transfer.id}>
                  <Table.Td>{formatDate(transfer.paid_on)}</Table.Td>
                  <Table.Td className={`${classes.amount} ja-figures`}>
                    {formatEuros(transfer.amount)}
                  </Table.Td>
                  <Table.Td>{transfer.reference ?? ''}</Table.Td>
                  <Table.Td className="ja-figures">{transfer.to ?? '–'}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      ) : (
        <div className={classes.empty}>
          <EmptyState>None yet.</EmptyState>
        </div>
      )}
    </Panel>
  );
}

function Payments({ money }: { money: TeamMoneyOut }) {
  const arrived = useArrivals(
    money.team.slug,
    money.payments.map((payment) => String(payment.id)),
  );
  return (
    <Panel
      title="Payments"
      flush
      actions={
        <Button component="a" href={money.export_url} size="xs" variant="default">
          Export
        </Button>
      }
    >
      {money.payments.length ? (
        <Table.ScrollContainer minWidth={640} type="native">
          <Table verticalSpacing="sm" aria-label="Payments">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Paid on</Table.Th>
                <Table.Th scope="col">Name</Table.Th>
                <Table.Th scope="col">Paid until</Table.Th>
                <Table.Th scope="col" className={classes.amount}>
                  Amount
                </Table.Th>
                <Table.Th scope="col" className={classes.amount}>
                  Counts
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {money.payments.map((payment) => (
                <Table.Tr key={payment.id} className={arrivedClass(arrived, payment.id)}>
                  <Table.Td>{payment.paid_at ? formatDayOf(payment.paid_at) : '–'}</Table.Td>
                  <Table.Td>{payment.name ?? '–'}</Table.Td>
                  <Table.Td>{payment.paid_until ? formatDate(payment.paid_until) : '–'}</Table.Td>
                  <Table.Td className={`${classes.amount} ja-figures`}>
                    {formatEuros(payment.amount)}
                  </Table.Td>
                  <Table.Td className={`${classes.amount} ja-figures`}>
                    {formatEuros(payment.counts)}
                    {payment.disputed ? (
                      <Text size="xs" c="var(--ja-warning)">
                        Taken back (chargeback)
                      </Text>
                    ) : payment.refunded ? (
                      <Text size="xs" c="dimmed">
                        {formatEuros(payment.refunded)} refunded
                      </Text>
                    ) : null}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      ) : (
        <div className={classes.empty}>
          <EmptyState>No payments yet.</EmptyState>
        </div>
      )}
    </Panel>
  );
}

export function TeamMoney() {
  const { slug = '' } = useParams();
  const money = useQuery(teamMoneyQuery(slug));
  useLiveRefresh(teamMoneyQuery(slug).queryKey, EVERY_MINUTE);
  const title = money.data?.team.name ?? slug;

  return (
    <>
      <PageHeader
        title={title}
        description="What the team's members paid, and what was passed on to the team."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Money', to: '/admin/money' }, { label: title }]}
      />
      {money.isPending ? (
        <LoadingState />
      ) : money.isError ? (
        <ErrorState error={money.error} onRetry={() => void money.refetch()} />
      ) : (
        <Stack gap="lg">
          <Balance money={money.data} />
          {/* Keyed by what was recorded, so the form starts afresh from the new balance. */}
          <TransferPanel
            key={`${String(money.data.open)}-${String(money.data.transfers.length)}`}
            money={money.data}
          />
          <BankPanel key={JSON.stringify(money.data.bank)} money={money.data} />
          <Periods money={money.data} />
          <Transfers money={money.data} />
          <Payments money={money.data} />
        </Stack>
      )}
    </>
  );
}
