/**
 * One announcement (docs/messages-plan.md): what it said, to whom, and how
 * far sending is -- asked again every half minute while it is under way.
 * Sending can be stopped: what has not gone out yet does not. Addresses that
 * failed are listed. Data: GET /api/v1/admin/announcements/<id>, POST
 * .../stop.
 */
import { Code, Stack, Table, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router';

import { api, call } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Details } from '../../../components/Details';
import { MailingProgress, MailingStatus } from '../../../components/Mailing';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatDateTime } from '../../../lib/format';
import { EVERY_30_SECONDS, useLiveRefresh } from '../../../lib/live';
import { notifyFailed } from '../../../lib/notify';

export function Announcement() {
  const { mailingId = '' } = useParams();
  const id = Number(mailingId);
  const client = useQueryClient();
  const key = ['admin', 'announcements', id] as const;
  const mailing = useQuery({
    queryKey: key,
    queryFn: () =>
      call(api.GET('/api/v1/admin/announcements/{mailing_id}', { params: { path: { mailing_id: id } } })),
  });
  useLiveRefresh(key, EVERY_30_SECONDS, mailing.data?.status === 'sending');
  const stop = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/admin/announcements/{mailing_id}/stop', { params: { path: { mailing_id: id } } }),
      ),
    onSuccess: (data) => {
      client.setQueryData(key, data);
      void client.invalidateQueries({ queryKey: ['admin', 'announcements'] });
    },
    onError: notifyFailed,
  });
  const crumbs = [
    { label: 'Admin', to: '/admin' },
    { label: 'Announcements', to: '/admin/announcements' },
    { label: mailing.data?.subject ?? 'Announcement' },
  ];
  if (mailing.isPending) return <LoadingState />;
  if (mailing.isError) return <ErrorState error={mailing.error} onRetry={() => void mailing.refetch()} />;
  const data = mailing.data;
  return (
    <>
      <PageHeader
        title={data.subject}
        crumbs={crumbs}
        actions={
          data.status === 'sending' ? (
            <ConfirmButton
              color="red"
              variant="default"
              confirmLabel="Yes, stop"
              loading={stop.isPending}
              onConfirm={() => {
                stop.mutate();
              }}
            >
              Stop sending
            </ConfirmButton>
          ) : null
        }
      />
      <Stack gap="lg">
        <Panel title="Sending" actions={<MailingStatus mailing={data} />}>
          <Stack gap="md">
            <MailingProgress mailing={data} />
            <Details
              items={[
                ['What', data.kind_label],
                ['To', data.audience_label],
                ['Written by', data.author ?? '—'],
                ['Started', formatDateTime(data.created_at)],
                ...(data.finished_at
                  ? [['Finished', formatDateTime(data.finished_at)] as [string, string]]
                  : []),
                ...(data.assembly_at
                  ? [['General assembly', formatDateTime(data.assembly_at)] as [string, string]]
                  : []),
              ]}
            />
            {data.status === 'sending' ? (
              <Text size="sm" c="dimmed">
                Sent a few at a time, within the mail provider&apos;s limits. This page follows along.
              </Text>
            ) : null}
          </Stack>
        </Panel>
        <Panel title="Text">
          <Code block style={{ whiteSpace: 'pre-wrap' }}>
            {data.body}
          </Code>
        </Panel>
        {data.failed_addresses.length ? (
          <Panel title="Not delivered" flush>
            <Table.ScrollContainer minWidth={420} type="native">
              <Table verticalSpacing="sm" aria-label="Not delivered">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th scope="col">Address</Table.Th>
                    <Table.Th scope="col">Why</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.failed_addresses.map((failed) => (
                    <Table.Tr key={failed.email}>
                      <Table.Td>{failed.email}</Table.Td>
                      <Table.Td>
                        <Text size="sm" c="dimmed">
                          {failed.error ?? '—'}
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Panel>
        ) : null}
      </Stack>
    </>
  );
}
