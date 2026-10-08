/**
 * The forum side of the account: its state there and what is in the way,
 * bringing it in line now, allowing one new profile picture; and the old
 * forum -- the archive this account came from, or reconnecting it to one by
 * hand when the address check could not (services/account_admin.py).
 */
import { Alert, Button, Group, Image, Stack, Table, Text, TextInput, VisuallyHidden } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router';

import { api, call } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Details } from '../../../components/Details';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { formatDate, formatDateTime, titleFromCode } from '../../../lib/format';
import { notifyDone, notifyNote } from '../../../lib/notify';
import { type Account, candidatesQuery, path, useAccountAction } from './shared';

function ForumActions({ account }: { account: Account }) {
  const resync = useAccountAction(
    () => call(api.POST('/api/v1/admin/accounts/{user_id}/forum-resync', path(account.id))),
    {
      done: (answer) => {
        if (answer.error) notifyNote(`Synced, with a problem: ${answer.error}`);
        else notifyDone('The forum is up to date.');
      },
    },
  );
  const allowed = account.membership?.picture_replacement_allowed_since ?? null;
  const picture = useAccountAction(
    (allow: boolean) =>
      call(
        api.PUT('/api/v1/admin/accounts/{user_id}/picture-replacement', {
          ...path(account.id),
          body: { allow },
        }),
      ),
    {
      done: (answer) => {
        notifyDone(answer.allowed_since ? 'A new picture is allowed.' : 'Permission withdrawn.');
      },
    },
  );
  if (!account.actions.resync_forum && !account.actions.picture_replacement) return null;
  return (
    <Stack gap="sm" mt="md">
      {account.actions.resync_forum ? (
        <Group gap="sm">
          <Button
            variant="default"
            loading={resync.isPending}
            onClick={() => {
              resync.mutate(undefined);
            }}
          >
            Sync with the forum
          </Button>
          <Text size="sm" c="dimmed">
            Sends the account&apos;s current state to the forum now.
          </Text>
        </Group>
      ) : null}
      {account.actions.picture_replacement ? (
        <Group gap="sm">
          <Button
            variant="default"
            loading={picture.isPending}
            onClick={() => {
              picture.mutate(!allowed);
            }}
          >
            {allowed ? 'Withdraw the new picture' : 'Allow a new picture'}
          </Button>
          <Text size="sm" c="dimmed">
            {allowed
              ? `Allowed since ${formatDateTime(allowed)}; the current picture stays until the new one is approved.`
              : 'Members cannot replace an approved picture on their own.'}
          </Text>
        </Group>
      ) : null}
    </Stack>
  );
}

function ForumPanel({ account }: { account: Account }) {
  const forum = account.forum;
  return (
    <Panel title="Forum">
      <Stack gap="sm">
        <Details
          items={[
            ['Status', forum.status_label],
            ['On the forum', forum.account_state ? titleFromCode(forum.account_state) : 'Not prepared'],
            ['Latest picture', forum.latest_picture ? titleFromCode(forum.latest_picture) : null],
            ['Last synced', forum.last_synced_at ? formatDateTime(forum.last_synced_at) : null],
          ]}
        />
        {forum.cleanup ? (
          <Alert color={forum.cleanup.failed ? 'red' : 'blue'} variant="light">
            {forum.cleanup.failed
              ? `The forum account this person left behind when reconnecting could not be removed, and holds their address: ${forum.cleanup.error ?? ''} Delete it in Discourse by hand (plain Delete, not "Delete and block"), then sync with the forum.`
              : 'The forum account this person left behind when reconnecting is being removed in the background; until then the forum refuses their address. Syncing with the forum removes it now.'}
          </Alert>
        ) : null}
        {forum.error ? (
          <Alert color="amber" variant="light" title="The last sync failed">
            {forum.error}
          </Alert>
        ) : null}
        {forum.review_note ? (
          <Text size="sm" c="dimmed">
            Review note: {forum.review_note}
          </Text>
        ) : null}
      </Stack>
      <ForumActions account={account} />
    </Panel>
  );
}

function OldForumPanel({ account }: { account: Account }) {
  const archive = account.old_forum;
  if (!archive) return null;
  return (
    <Panel
      title="Old forum account"
      actions={
        archive.claimed_at ? <Pill tone="active">Reconnected</Pill> : <Pill tone="neutral">Not claimed</Pill>
      }
    >
      <Group align="flex-start" gap="lg" wrap="nowrap">
        {archive.picture_url ? (
          <Image src={archive.picture_url} alt={archive.display_name} w={72} h={72} fit="cover" />
        ) : null}
        <Stack gap="sm" flex={1}>
          <Details
            items={[
              ['Name on the forum', archive.display_name],
              ['Username', archive.username],
              ['Year group', archive.year_group],
              ['Registered with', archive.email],
              [
                'Group there',
                archive.group ? (
                  <>
                    {archive.group}
                    {archive.group_reason ? (
                      <Text size="sm" c="dimmed">
                        {archive.group_reason}
                      </Text>
                    ) : null}
                  </>
                ) : (
                  'Unknown'
                ),
              ],
              ['Posts', archive.posts === null ? null : String(archive.posts)],
              ['Joined', archive.joined_on ? formatDate(archive.joined_on) : null],
              ['Last posted', archive.last_posted_on ? formatDate(archive.last_posted_on) : 'Never'],
              ...(archive.claimed_at
                ? ([['Reconnected', formatDateTime(archive.claimed_at)]] as [string, string][])
                : []),
            ]}
          />
          {archive.claimed_at ? null : (
            <Text size="sm" c="dimmed">
              Reconnected by itself when somebody signs up and confirms the address it was registered with.
              Until then it keeps the old posts&apos; name and face.
            </Text>
          )}
        </Stack>
      </Group>
    </Panel>
  );
}

const LIKELY_BECAUSE = { address: 'Differs only in dots or spelling', name: 'Same name' } as const;

function Reconnect({ account }: { account: Account }) {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [q] = useDebouncedValue(search.trim(), 300);
  const candidates = useQuery({ ...candidatesQuery(account.id, q.length >= 2 ? q : '') });
  const reconnect = useAccountAction(
    (profileId: number) =>
      call(
        api.POST('/api/v1/admin/accounts/{user_id}/reconnect', {
          ...path(account.id),
          body: { profile_id: profileId },
        }),
      ),
    {
      done: (answer) => {
        notifyDone(`Reconnected to ${answer.old_username}.`);
        if (answer.forum === 'background')
          notifyNote('The forum is updated in the background over the next minutes.');
        else if (answer.forum_error) notifyNote(`The forum could not be updated: ${answer.forum_error}`);
        // The account lives on the old forum's row now; the one it came from is gone.
        void navigate(`/admin/accounts/${String(answer.account_id)}#forum`, { replace: true });
      },
    },
  );

  return (
    <Panel title="Reconnect to the old forum">
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          For a returning member whose university address no longer matches the old forum&apos;s: a new name,
          an address that stopped working, or none on file. Recognising them is the proof.
        </Text>
        <TextInput
          type="search"
          label="Search the old forum"
          placeholder="Old username, name or address"
          value={search}
          onChange={(event) => {
            setSearch(event.currentTarget.value);
          }}
        />
        {candidates.isPending ? (
          <LoadingState />
        ) : candidates.isError ? (
          <ErrorState error={candidates.error} onRetry={() => void candidates.refetch()} />
        ) : candidates.data.items.length === 0 ? (
          <EmptyState>
            {candidates.data.likely
              ? 'Nothing on the old forum looks like theirs. Search for them.'
              : 'No old forum account nobody has claimed matches.'}
          </EmptyState>
        ) : (
          <>
            {candidates.data.likely ? (
              <Text size="sm">
                Probably theirs. The old forum never checked addresses, so it may hold a mistyped one.
                Reconnect only if you recognise them.
              </Text>
            ) : null}
            <Table.ScrollContainer minWidth={640} type="native">
              <Table verticalSpacing="xs" aria-label="Old forum accounts">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th scope="col">Username</Table.Th>
                    <Table.Th scope="col">Name</Table.Th>
                    <Table.Th scope="col">Year group</Table.Th>
                    <Table.Th scope="col">Address there</Table.Th>
                    <Table.Th scope="col">Posts</Table.Th>
                    <Table.Th scope="col">
                      <VisuallyHidden>Action</VisuallyHidden>
                    </Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {candidates.data.items.map((candidate) => (
                    <Table.Tr key={candidate.id}>
                      <Table.Td>{candidate.username}</Table.Td>
                      <Table.Td>{candidate.display_name}</Table.Td>
                      <Table.Td>{candidate.year_group ?? '–'}</Table.Td>
                      <Table.Td>
                        {candidate.email ?? '–'}
                        {candidate.likely_because ? (
                          <Text size="xs" c="var(--ja-warning)">
                            {LIKELY_BECAUSE[candidate.likely_because]}
                          </Text>
                        ) : null}
                      </Table.Td>
                      <Table.Td className="ja-figures">{candidate.posts ?? '–'}</Table.Td>
                      <Table.Td>
                        <ConfirmButton
                          size="xs"
                          color="brand"
                          confirmLabel="Yes, reconnect"
                          loading={reconnect.isPending && reconnect.variables === candidate.id}
                          onConfirm={() => {
                            reconnect.mutate(candidate.id);
                          }}
                        >
                          Reconnect
                        </ConfirmButton>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </>
        )}
      </Stack>
    </Panel>
  );
}

export function ForumTab({ account }: { account: Account }) {
  return (
    <Stack gap="lg">
      <ForumPanel account={account} />
      <OldForumPanel account={account} />
      {account.actions.reconnect ? <Reconnect account={account} /> : null}
    </Stack>
  );
}
