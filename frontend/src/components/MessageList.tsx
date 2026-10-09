/**
 * Messages from one person, as their readers see them (docs/messages-plan.md):
 * the admins' (Admin › Messages) or one team's leads' (the team's Messages
 * page). Open ones first; each answered by email -- a reply opens one's own
 * mail program, addressed to the sender -- and marked done once answered.
 * Done ones are kept a year.
 */
import { Anchor, Button, Group, SegmentedControl, Stack, Text } from '@mantine/core';
import { IconMail } from '@tabler/icons-react';
import { type QueryKey, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { Schemas } from '../api/client';
import { AppLink } from '../app/AppLink';
import { formatDateTime } from '../lib/format';
import { EVERY_MINUTE, useLiveRefresh } from '../lib/live';
import { notifyFailed } from '../lib/notify';
import { Panel } from './Panel';
import { Pill } from './Pill';
import { EmptyState, ErrorState, LoadingState } from './States';

export type ContactMessage = Schemas['ContactMessageOut'];
type Messages = Schemas['ContactMessagesOut'];
export type MessageState = 'open' | 'done' | 'all';

interface MessageListProps {
  queryKey: QueryKey;
  load: (state: MessageState) => Promise<Messages>;
  markDone: (id: number, done: boolean) => Promise<ContactMessage>;
  /** Admins see a link to the sender's account. */
  accountLinks?: boolean;
  /** After one is marked: the menu's count, say. */
  onChanged?: () => void;
}

function replyHref(message: ContactMessage) {
  const subject = message.subject.toLowerCase().startsWith('re:')
    ? message.subject
    : `Re: ${message.subject}`;
  return `mailto:${message.sender_email}?subject=${encodeURIComponent(subject)}`;
}

function MessageCard({
  message,
  accountLinks,
  onDone,
  busy,
}: {
  message: ContactMessage;
  accountLinks: boolean;
  onDone: (done: boolean) => void;
  busy: boolean;
}) {
  const facts = [
    message.topic_label,
    formatDateTime(message.created_at),
    message.page ? `from ${message.page}` : null,
  ].filter(Boolean);
  return (
    <Panel
      title={message.subject}
      actions={message.done_at ? <Pill tone="neutral">Done</Pill> : <Pill tone="pending">Open</Pill>}
    >
      <Stack gap="sm">
        <Group gap="xs" wrap="wrap">
          <Text size="sm" fw={600}>
            {message.sender_name}
          </Text>
          <Anchor href={`mailto:${message.sender_email}`} size="sm">
            {message.sender_email}
          </Anchor>
          {accountLinks && message.account_id ? (
            <Anchor component={AppLink} to={`/admin/accounts/${String(message.account_id)}`} size="sm">
              {`Account no. ${String(message.account_id)}${message.membership ? `, ${message.membership}` : ''}`}
            </Anchor>
          ) : null}
        </Group>
        <Text size="xs" c="dimmed">
          {facts.join(' · ')}
          {message.delivered ? '' : ' · its email has not gone out yet'}
        </Text>
        <Text size="sm" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          {message.body}
        </Text>
        <Group gap="sm">
          <Button
            component="a"
            href={replyHref(message)}
            size="xs"
            leftSection={<IconMail size={14} aria-hidden />}
          >
            Answer by email
          </Button>
          <Button
            size="xs"
            variant="default"
            loading={busy}
            onClick={() => {
              onDone(!message.done_at);
            }}
          >
            {message.done_at ? 'Open again' : 'Mark done'}
          </Button>
          {message.done_at ? (
            <Text size="xs" c="dimmed">
              {`Done ${formatDateTime(message.done_at)}${message.done_by ? ` by ${message.done_by}` : ''}`}
            </Text>
          ) : null}
        </Group>
      </Stack>
    </Panel>
  );
}

export function MessageList({ queryKey, load, markDone, accountLinks = false, onChanged }: MessageListProps) {
  const [state, setState] = useState<MessageState>('open');
  const client = useQueryClient();
  const key = [...queryKey, state];
  const list = useQuery({ queryKey: key, queryFn: () => load(state) });
  useLiveRefresh(key, EVERY_MINUTE);
  const [busyId, setBusyId] = useState<number | null>(null);
  const done = useMutation({
    mutationFn: ({ id, value }: { id: number; value: boolean }) => markDone(id, value),
    onMutate: ({ id }) => {
      setBusyId(id);
    },
    onSettled: () => {
      setBusyId(null);
      void client.invalidateQueries({ queryKey });
      onChanged?.();
    },
    onError: notifyFailed,
  });

  return (
    <Stack gap="lg">
      <SegmentedControl
        value={state}
        onChange={(value) => {
          setState(value);
        }}
        data={[
          {
            value: 'open',
            label: list.data && state === 'open' ? `Open (${String(list.data.open_count)})` : 'Open',
          },
          { value: 'done', label: 'Done' },
          { value: 'all', label: 'All' },
        ]}
        aria-label="Which messages"
        style={{ alignSelf: 'flex-start' }}
      />
      {list.isPending ? (
        <LoadingState />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.items.length ? (
        list.data.items.map((message) => (
          <MessageCard
            key={message.id}
            message={message}
            accountLinks={accountLinks}
            busy={busyId === message.id}
            onDone={(value) => {
              done.mutate({ id: message.id, value });
            }}
          />
        ))
      ) : (
        <EmptyState>{state === 'open' ? 'Nothing open. All answered.' : 'No messages.'}</EmptyState>
      )}
    </Stack>
  );
}
