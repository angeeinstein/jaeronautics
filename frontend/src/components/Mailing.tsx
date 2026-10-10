/**
 * Writing to many members, and what was sent (docs/messages-plan.md): the
 * subject and text -- simple formatting, Markdown -- a test to oneself,
 * then sending to the number of people shown; and the list of what went
 * out, with how far each is. Shared by Admin › Announcements and a team's
 * Messages page.
 */
import { Anchor, Button, Group, Progress, Stack, Table, Text, Textarea, TextInput } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { type ReactNode, useState } from 'react';

import { ApiError, type Schemas } from '../api/client';
import { AppLink } from '../app/AppLink';
import { formatDateTime, plural } from '../lib/format';
import { notifyDone, notifyFailed } from '../lib/notify';
import { ConfirmButton } from './ConfirmButton';
import { Pill } from './Pill';
import { EmptyState } from './States';

export type Mailing = Schemas['MailingOut'];
export interface Draft {
  subject: string;
  body: string;
}

const STATUS: Record<Mailing['status'], { tone: 'active' | 'pending' | 'neutral'; label: string }> = {
  sending: { tone: 'pending', label: 'Sending' },
  sent: { tone: 'active', label: 'Sent' },
  stopped: { tone: 'neutral', label: 'Stopped' },
};

export function MailingStatus({ mailing }: { mailing: Mailing }) {
  const status = STATUS[mailing.status];
  return <Pill tone={status.tone}>{status.label}</Pill>;
}

/** How far it is: sent and failed of everybody it goes to. */
export function MailingProgress({ mailing }: { mailing: Mailing }) {
  const total = Math.max(mailing.recipient_count, 1);
  return (
    <Stack gap={4}>
      <Progress.Root size="sm" aria-label="Sent">
        <Progress.Section value={(mailing.sent / total) * 100} color="brand" />
        <Progress.Section value={(mailing.failed / total) * 100} color="red" />
      </Progress.Root>
      <Text size="xs" c="dimmed">
        {`${String(mailing.sent)} of ${String(mailing.recipient_count)} sent`}
        {mailing.failed ? `, ${String(mailing.failed)} failed` : ''}
        {mailing.waiting ? `, ${String(mailing.waiting)} waiting` : ''}
        {mailing.unsubscribed_count ? `; ${String(mailing.unsubscribed_count)} switched the news off` : ''}
      </Text>
    </Stack>
  );
}

interface ComposerProps {
  draft: Draft;
  onDraft: (draft: Draft) => void;
  /** Above the subject: who it is for, what kind. */
  before?: ReactNode;
  /** How many it would reach now; null while not known. */
  recipients: number | null;
  test: (draft: Draft) => Promise<{ to: string }>;
  send: (draft: Draft) => Promise<Mailing>;
  onSent: (mailing: Mailing) => void;
  /** Something stands in the way, said beside the button. */
  blocked?: string | null;
}

export function MailingComposer({
  draft,
  onDraft,
  before,
  recipients,
  test,
  send,
  onSent,
  blocked,
}: ComposerProps) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const onError = (error: unknown) => {
    if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
    else notifyFailed(error);
  };
  const testing = useMutation({
    mutationFn: () => test(draft),
    onSuccess: (result) => {
      setErrors({});
      notifyDone(`A test went to ${result.to}.`);
    },
    onError,
  });
  const sending = useMutation({
    mutationFn: () => send(draft),
    onSuccess: (mailing) => {
      setErrors({});
      onSent(mailing);
    },
    onError,
  });
  const ready = draft.subject.trim() !== '' && draft.body.trim() !== '';
  const people =
    recipients === null ? '…' : `${String(recipients)} ${plural(recipients, 'person', 'people')}`;
  return (
    <Stack gap="md">
      {before}
      <TextInput
        label="Subject"
        required
        maxLength={150}
        value={draft.subject}
        error={errors.subject}
        onChange={(event) => {
          onDraft({ ...draft, subject: event.currentTarget.value });
        }}
      />
      <Textarea
        label="Text"
        description="**bold**, *italic*, - lists, [a link](https://…). Send yourself a test to see it."
        inputWrapperOrder={['label', 'input', 'description', 'error']}
        required
        autosize
        minRows={8}
        maxLength={20000}
        value={draft.body}
        error={errors.body ?? errors.audience ?? errors.assembly_at}
        onChange={(event) => {
          onDraft({ ...draft, body: event.currentTarget.value });
        }}
      />
      <Group gap="sm" wrap="wrap">
        <Button
          variant="default"
          disabled={!ready}
          loading={testing.isPending}
          onClick={() => {
            testing.mutate();
          }}
        >
          Send a test to me
        </Button>
        <ConfirmButton
          confirmLabel={`Yes, send to ${people}`}
          disabled={!ready || !recipients || Boolean(blocked)}
          loading={sending.isPending}
          onConfirm={() => {
            sending.mutate();
          }}
        >
          {`Send to ${people}`}
        </ConfirmButton>
        {blocked ? (
          <Text size="sm" c="dimmed">
            {blocked}
          </Text>
        ) : null}
      </Group>
    </Stack>
  );
}

/** What was sent, newest first; each a way to its page when ``linkTo`` gives one. */
export function MailingTable({ items, linkTo }: { items: Mailing[]; linkTo?: (mailing: Mailing) => string }) {
  if (!items.length) return <EmptyState>Nothing sent yet.</EmptyState>;
  return (
    <Table.ScrollContainer minWidth={560} type="native">
      <Table verticalSpacing="sm" aria-label="Sent">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Sent</Table.Th>
            <Table.Th scope="col">Subject</Table.Th>
            <Table.Th scope="col">To</Table.Th>
            <Table.Th scope="col" w={200}>
              How far
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {items.map((mailing) => (
            <Table.Tr key={mailing.id}>
              <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(mailing.created_at)}</Table.Td>
              <Table.Td>
                <Stack gap={2}>
                  {linkTo ? (
                    <Anchor component={AppLink} to={linkTo(mailing)} fw={600}>
                      {mailing.subject}
                    </Anchor>
                  ) : (
                    <Text fw={600}>{mailing.subject}</Text>
                  )}
                  <Group gap={6}>
                    <MailingStatus mailing={mailing} />
                    <Text size="xs" c="dimmed">
                      {[mailing.kind_label, mailing.author].filter(Boolean).join(' · ')}
                    </Text>
                  </Group>
                </Stack>
              </Table.Td>
              <Table.Td>
                <Text size="sm">{mailing.audience_label}</Text>
              </Table.Td>
              <Table.Td>
                <MailingProgress mailing={mailing} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
