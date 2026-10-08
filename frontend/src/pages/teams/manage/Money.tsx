/**
 * A team's money, the team's side: what its members paid, what the
 * association transferred and what is open; the account it is paid to
 * (changed by whoever may, logged, the association's treasurer told); and
 * every period, transfer and payment. Transfers are recorded on the
 * association's side (Admin › Money). Data: GET /api/v1/teams/<slug>/money,
 * PUT .../money/bank.
 */
import { Button, Group, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../../api/client';
import { AppLink } from '../../../app/AppLink';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { StatTile } from '../../../components/StatTile';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { arrivedClass, useArrivals } from '../../../lib/arrivals';
import { formatDate, formatDayOf, formatEuros } from '../../../lib/format';
import { EVERY_MINUTE, useLiveRefresh } from '../../../lib/live';
import { notifyDone, notifyFailed, notifyNote } from '../../../lib/notify';
import { useSlug } from './shared';

type Funds = Schemas['TeamFundsOut'];

const fundsKey = (slug: string) => ['teams', slug, 'money'] as const;

function Bank({ funds }: { funds: Funds }) {
  const slug = useSlug();
  const client = useQueryClient();
  const [holder, setHolder] = useState(funds.bank.account_holder ?? '');
  const [iban, setIban] = useState(funds.bank.iban ?? '');
  const [bic, setBic] = useState(funds.bank.bic ?? '');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      call(
        api.PUT('/api/v1/teams/{slug}/money/bank', {
          params: { path: { slug } },
          body: { account_holder: holder || null, iban: iban || null, bic: bic || null },
        }),
      ),
    onSuccess: ({ changed, funds: fresh }) => {
      setErrors({});
      setProblem(null);
      client.setQueryData(fundsKey(slug), fresh);
      if (changed) notifyDone('Bank details saved.');
      else notifyNote('Nothing changed.');
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else if (error instanceof ApiError && error.status === 400) setProblem(error.message);
      else notifyFailed(error);
    },
  });
  return (
    <Panel title="Bank details">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Where the association transfers the team's money. Every change is logged, and the association's
          treasurer is told.
        </Text>
        {funds.may_edit_bank ? (
          <>
            <TextInput
              label="Account holder"
              maxLength={70}
              autoComplete="off"
              value={holder}
              error={errors.account_holder ?? null}
              onChange={(event) => {
                setHolder(event.currentTarget.value);
              }}
            />
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              <TextInput
                label="IBAN"
                maxLength={42}
                autoComplete="off"
                spellCheck={false}
                value={iban}
                error={errors.iban ?? problem}
                onChange={(event) => {
                  setIban(event.currentTarget.value);
                  setProblem(null);
                }}
              />
              <TextInput
                label="BIC (optional)"
                maxLength={11}
                autoComplete="off"
                spellCheck={false}
                value={bic}
                error={errors.bic ?? null}
                onChange={(event) => {
                  setBic(event.currentTarget.value);
                }}
              />
            </SimpleGrid>
            <Group justify="flex-end">
              <Button
                loading={save.isPending}
                onClick={() => {
                  save.mutate();
                }}
              >
                Save
              </Button>
            </Group>
          </>
        ) : funds.bank.iban ? (
          <Text>{`${funds.bank.account_holder ?? ''}, ${funds.bank.iban}`}</Text>
        ) : (
          <EmptyState>None yet.</EmptyState>
        )}
      </Stack>
    </Panel>
  );
}

function Body({ funds }: { funds: Funds }) {
  const arrived = useArrivals(
    funds.slug,
    funds.payments.map((payment) => String(payment.id)),
  );
  return (
    <Stack gap="lg">
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
        <StatTile value={formatEuros(funds.earned)} label="Paid by members" note="Less refunds" />
        <StatTile
          value={formatEuros(funds.paid_out)}
          label="Transferred to the team"
          note={funds.last_transfer_on ? `Last on ${formatDate(funds.last_transfer_on)}` : 'None yet'}
        />
        <StatTile value={formatEuros(funds.open)} label="Open" note="Still to be transferred" />
      </SimpleGrid>
      <Text size="sm" c="dimmed">
        The team gets exactly what its members paid, less refunds. Stripe's fees are paid by the association.
      </Text>
      <Bank
        key={`${funds.bank.account_holder ?? ''}|${funds.bank.iban ?? ''}|${funds.bank.bic ?? ''}`}
        funds={funds}
      />
      <Panel title="By period" flush>
        {funds.by_period.length ? (
          <Table verticalSpacing="sm" aria-label="By period">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Paid until</Table.Th>
                <Table.Th scope="col" ta="right">
                  Paid by members
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {funds.by_period.map((period) => (
                <Table.Tr key={period.paid_until ?? 'none'}>
                  <Table.Td>{period.paid_until ? formatDate(period.paid_until) : '–'}</Table.Td>
                  <Table.Td ta="right" className="ja-figures">
                    {formatEuros(period.earned)}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <Stack px="md">
            <EmptyState>No payments yet.</EmptyState>
          </Stack>
        )}
      </Panel>
      <Panel
        title="Transfers"
        flush
        actions={
          <Text size="sm" c="dimmed">
            {funds.transfers.length}
          </Text>
        }
      >
        {funds.transfers.length ? (
          <Table.ScrollContainer minWidth={520} type="native">
            <Table verticalSpacing="sm" aria-label="Transfers">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Sent on</Table.Th>
                  <Table.Th scope="col" ta="right">
                    Amount
                  </Table.Th>
                  <Table.Th scope="col">Reference</Table.Th>
                  <Table.Th scope="col">To</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {funds.transfers.map((transfer) => (
                  <Table.Tr key={transfer.id}>
                    <Table.Td>{formatDate(transfer.paid_on)}</Table.Td>
                    <Table.Td ta="right" className="ja-figures">
                      {formatEuros(transfer.amount)}
                    </Table.Td>
                    <Table.Td>{transfer.reference ?? ''}</Table.Td>
                    <Table.Td>{transfer.to ?? '–'}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Stack px="md">
            <EmptyState>None yet.</EmptyState>
          </Stack>
        )}
      </Panel>
      <Panel
        title="Payments"
        flush
        actions={
          <Text size="sm" c="dimmed">
            {funds.payments.length}
          </Text>
        }
      >
        {funds.payments.length ? (
          <Table.ScrollContainer minWidth={640} type="native">
            <Table verticalSpacing="sm" aria-label="Payments">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Paid on</Table.Th>
                  <Table.Th scope="col">Name</Table.Th>
                  <Table.Th scope="col">Paid until</Table.Th>
                  <Table.Th scope="col" ta="right">
                    Amount
                  </Table.Th>
                  <Table.Th scope="col" ta="right">
                    Counts
                  </Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {funds.payments.map((payment) => (
                  <Table.Tr key={payment.id} className={arrivedClass(arrived, payment.id)}>
                    <Table.Td>{payment.paid_at ? formatDayOf(payment.paid_at) : '–'}</Table.Td>
                    <Table.Td>{payment.name ?? '–'}</Table.Td>
                    <Table.Td>{payment.paid_until ? formatDate(payment.paid_until) : '–'}</Table.Td>
                    <Table.Td ta="right" className="ja-figures">
                      {formatEuros(payment.amount)}
                    </Table.Td>
                    <Table.Td ta="right">
                      <span className="ja-figures">{formatEuros(payment.counts)}</span>
                      {payment.disputed ? (
                        <Text size="xs" c="var(--ja-warning)">
                          Taken back (chargeback)
                        </Text>
                      ) : payment.refunded ? (
                        <Text size="xs" c="dimmed">{`${formatEuros(payment.refunded)} refunded`}</Text>
                      ) : null}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Stack px="md">
            <EmptyState>No payments yet.</EmptyState>
          </Stack>
        )}
      </Panel>
    </Stack>
  );
}

export function Money() {
  const slug = useSlug();
  const funds = useQuery({
    queryKey: fundsKey(slug),
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/money', { params: { path: { slug } } })),
  });
  useLiveRefresh(fundsKey(slug), EVERY_MINUTE);
  const name = funds.data?.name ?? slug;
  return (
    <>
      <PageHeader
        title="Money"
        description="What the members paid, what was transferred to the team, and what is open."
        crumbs={[{ label: 'Teams', to: '/teams' }, { label: name, to: `/teams/${slug}` }, { label: 'Money' }]}
        actions={
          funds.data ? (
            <>
              {funds.data.records_transfers ? (
                <Button component={AppLink} to={`/admin/money/${slug}`} variant="default">
                  Transfers (Admin)
                </Button>
              ) : null}
              {/* A download from Flask, which logs it. */}
              <Button component="a" href={funds.data.export_url} variant="default">
                Export payments
              </Button>
            </>
          ) : null
        }
      />
      {funds.isPending ? (
        <LoadingState />
      ) : funds.isError ? (
        <ErrorState error={funds.error} onRetry={() => void funds.refetch()} />
      ) : (
        <Body funds={funds.data} />
      )}
    </>
  );
}
