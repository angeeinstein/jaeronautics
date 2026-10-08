/**
 * One thing waiting for a decision: who asked, what for, and the decision --
 * Approve (the main action) and Reject (two clicks), with a note the member
 * gets either way. A name change also asks whether the forum username should
 * follow.
 */
import {
  Avatar,
  Button,
  Checkbox,
  Group,
  Image,
  Stack,
  Table,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useState } from 'react';

import { api, call } from '../../../api/client';
import { AppLink } from '../../../app/AppLink';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Pill } from '../../../components/Pill';
import { formatDateTime } from '../../../lib/format';
import { notifyDone, notifyNote } from '../../../lib/notify';
import { type QueueItem, useDecision } from './shared';
import classes from './Reviews.module.css';

function initialsOf(name: string) {
  const words = name.split(/\s+/).filter(Boolean);
  return (words.length > 1 ? [words[0]?.[0], words.at(-1)?.[0]] : [words[0]?.[0]]).join('').toUpperCase();
}

function headline(item: QueueItem) {
  if (item.kind === 'picture') return 'Profile picture';
  if (item.change?.is_name_change) return 'Name change';
  return 'Details change';
}

function Changes({ item }: { item: QueueItem }) {
  if (!item.change) return null;
  return (
    <Table className={classes.changes} withRowBorders={false} verticalSpacing={4} aria-label="What changes">
      <Table.Tbody>
        {item.change.changes.map((change) => (
          <Table.Tr key={change.field}>
            <Table.Th scope="row" className={classes.changeLabel}>
              {change.label}
            </Table.Th>
            <Table.Td className={classes.was}>{change.current ?? 'none'}</Table.Td>
            <Table.Td>→ {change.requested ?? 'none'}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

export function ReviewCard({
  item,
  canOpenAccount,
  arrived = false,
}: {
  item: QueueItem;
  canOpenAccount: boolean;
  /** Came in while the page was open (lib/arrivals.ts): marked for a moment. */
  arrived?: boolean;
}) {
  const [note, setNote] = useState('');
  const username = item.change?.forum_username ?? null;
  const [rename, setRename] = useState(false);
  const [newUsername, setNewUsername] = useState(username?.suggested ?? '');
  const isPicture = item.kind === 'picture';
  const base = isPicture ? 'pictures' : 'name-changes';

  const approve = useDecision(
    async () => {
      if (isPicture) {
        const answer = await call(
          api.POST('/api/v1/admin/reviews/pictures/{submission_id}/approve', {
            params: { path: { submission_id: item.id } },
            body: { note: note.trim() || null },
          }),
        );
        return { forumError: answer.forum_error, renamePending: false };
      }
      const answer = await call(
        api.POST('/api/v1/admin/reviews/name-changes/{request_id}/approve', {
          params: { path: { request_id: item.id } },
          body: { note: note.trim() || null, forum_username: rename ? newUsername.trim() : null },
        }),
      );
      return { forumError: answer.forum_error, renamePending: answer.rename_pending };
    },
    ({ forumError, renamePending }) => {
      notifyDone(isPicture ? 'Approved and sent to the forum.' : 'Change approved.');
      if (renamePending)
        notifyNote('The forum could not be renamed just now; it is tried again automatically.');
      if (forumError) notifyNote(`The forum could not be updated: ${forumError}`);
    },
  );
  const reject = useDecision(
    () =>
      isPicture
        ? call(
            api.POST('/api/v1/admin/reviews/pictures/{submission_id}/reject', {
              params: { path: { submission_id: item.id } },
              body: { note: note.trim() || null },
            }),
          )
        : call(
            api.POST('/api/v1/admin/reviews/name-changes/{request_id}/reject', {
              params: { path: { request_id: item.id } },
              body: { note: note.trim() || null },
            }),
          ),
    () => {
      notifyDone(isPicture ? 'Picture rejected. They are asked for another.' : 'Change rejected.');
    },
  );
  const busy = approve.isPending || reject.isPending;

  return (
    <section
      className={arrived ? `${classes.card} ja-arrived-card` : classes.card}
      aria-label={`${headline(item)}: ${item.person.name}`}
      id={`${base}-${String(item.id)}`}
    >
      <Group gap="sm" wrap="nowrap" align="flex-start">
        <Avatar src={item.person.picture_url} alt="" size={36} color="brand" variant="light" aria-hidden>
          {initialsOf(item.person.name)}
        </Avatar>
        <Stack gap={2} className={classes.who}>
          <Group gap={8}>
            {canOpenAccount && item.person.user_id ? (
              <AppLink to={`/admin/accounts/${String(item.person.user_id)}`} className={classes.name}>
                {item.person.name}
              </AppLink>
            ) : (
              <Text fw={600}>{item.person.name}</Text>
            )}
            <Pill tone="info">{headline(item)}</Pill>
          </Group>
          <Text size="sm" c="dimmed">
            {[
              item.person.email,
              item.picture?.forum_username ? `forum name ${item.picture.forum_username}` : null,
            ]
              .filter(Boolean)
              .join(' · ')}
            {' · '}
            {isPicture ? 'uploaded ' : 'asked '}
            {formatDateTime(item.at)}
          </Text>
          {item.change?.is_name_change ? (
            <Text size="sm">
              {item.person.name} → <strong>{item.change.requested_full_name}</strong>
            </Text>
          ) : null}
          {item.change?.member_note ? (
            <Text size="sm" fs="italic">
              “{item.change.member_note}”
            </Text>
          ) : null}
        </Stack>
      </Group>

      <Group align="flex-start" gap="lg" mt="md" wrap="wrap">
        {item.picture?.image_url ? (
          <Image
            src={item.picture.image_url}
            alt={`Profile picture uploaded by ${item.person.name}`}
            className={classes.photo}
            loading="lazy"
          />
        ) : null}
        <Stack gap="sm" className={classes.decision}>
          <Changes item={item} />
          {username ? (
            <Stack gap={6}>
              <Checkbox
                checked={rename}
                onChange={(event) => {
                  setRename(event.currentTarget.checked);
                }}
                label={`Also change the forum username from ${username.current} to`}
              />
              <TextInput
                aria-label="New forum username"
                value={newUsername}
                disabled={!rename}
                onChange={(event) => {
                  setNewUsername(event.currentTarget.value);
                }}
                description="A number is added if the name is taken."
                maw={360}
              />
            </Stack>
          ) : null}
          <Textarea
            label="Note to the member (optional)"
            placeholder="Sent with the approval or the rejection"
            autosize
            minRows={2}
            maxLength={2000}
            value={note}
            onChange={(event) => {
              setNote(event.currentTarget.value);
            }}
          />
          <Group gap="sm">
            <Button
              loading={approve.isPending}
              disabled={busy && !approve.isPending}
              onClick={() => {
                approve.mutate(undefined);
              }}
            >
              {isPicture ? 'Approve and send to forum' : 'Approve'}
            </Button>
            <ConfirmButton
              confirmLabel="Yes, reject"
              loading={reject.isPending}
              disabled={busy && !reject.isPending}
              onConfirm={() => {
                reject.mutate(undefined);
              }}
            >
              Reject
            </ConfirmButton>
          </Group>
        </Stack>
      </Group>
    </section>
  );
}
