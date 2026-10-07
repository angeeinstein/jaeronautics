/**
 * A person, as a team's leads see them: how to reach them, what is under
 * way -- their application, with inviting, approving or not accepting; or
 * their membership, with removing -- their history in the team, and the
 * leads' notes. Data: GET /api/v1/teams/<slug>/manage/people/<user> and the
 * decisions under .../memberships/<id>/.
 */
import { Button, Group, SimpleGrid, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router';

import { api, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Details } from '../../../components/Details';
import { Panel } from '../../../components/Panel';
import { Pill, type Tone } from '../../../components/Pill';
import { ErrorState, LoadingState } from '../../../components/States';
import { formatDate, formatDateTime } from '../../../lib/format';
import classes from '../Teams.module.css';
import { ManageHeader, useManageChange, useSlug } from './shared';

type Person = Schemas['TeamPersonOut'];

const TONES: Record<string, Tone> = {
  applied: 'pending',
  invited: 'info',
  approved: 'info',
  active: 'active',
  ended: 'neutral',
  rejected: 'failed',
  withdrawn: 'neutral',
};

function Now({ person, personKey }: { person: Person; personKey: readonly unknown[] }) {
  const slug = useSlug();
  const now = person.now;
  const [meeting, setMeeting] = useState(now?.meeting ?? '');
  const [reason, setReason] = useState('');
  const path = { slug, membership_id: now?.membership_id ?? 0 };
  const invite = useManageChange(
    personKey,
    () =>
      call(
        api.POST('/api/v1/teams/{slug}/manage/memberships/{membership_id}/invite', {
          params: { path },
          body: { meeting_details: meeting },
        }),
      ),
    'Invitation sent.',
  );
  const decide = useManageChange(
    personKey,
    (decision: 'approve' | 'reject') =>
      call(
        decision === 'approve'
          ? api.POST('/api/v1/teams/{slug}/manage/memberships/{membership_id}/approve', { params: { path } })
          : api.POST('/api/v1/teams/{slug}/manage/memberships/{membership_id}/reject', { params: { path } }),
      ),
    (out) => (out.now?.status === 'active' || out.now?.status === 'approved' ? 'Approved.' : 'Not accepted.'),
  );
  const remove = useManageChange(
    personKey,
    () =>
      call(
        api.POST('/api/v1/teams/{slug}/manage/memberships/{membership_id}/remove', {
          params: { path },
          body: { reason },
        }),
      ),
    'Removed from the team.',
  );
  if (!now) return null;
  const deciding = now.status === 'applied' || now.status === 'invited';
  return (
    <Panel title="Now" actions={<Pill tone={TONES[now.status] ?? 'neutral'}>{now.status_label}</Pill>}>
      <Stack gap="md">
        {now.answer ? (
          <div>
            <Text size="sm" c="dimmed">
              {now.question ?? 'Application'}
            </Text>
            <Text size="sm" className={classes.preLine}>
              {now.answer}
            </Text>
          </div>
        ) : null}
        {deciding && person.may_review ? (
          <>
            <Stack gap="xs">
              <Textarea
                label="Invite to a meeting"
                placeholder="When and where, or a link"
                autosize
                minRows={3}
                value={meeting}
                error={invite.errors.meeting_details ?? null}
                onChange={(event) => {
                  setMeeting(event.currentTarget.value);
                }}
              />
              <Group>
                <Button
                  variant="default"
                  disabled={!meeting.trim()}
                  loading={invite.mutation.isPending}
                  onClick={() => {
                    invite.mutation.mutate(undefined);
                  }}
                >
                  {now.status === 'applied' ? 'Send invitation' : 'Send again'}
                </Button>
              </Group>
            </Stack>
            <Group>
              <ConfirmButton
                color="brand"
                confirmLabel="Yes, approve"
                loading={decide.mutation.isPending && decide.mutation.variables === 'approve'}
                onConfirm={() => {
                  decide.mutation.mutate('approve');
                }}
              >
                Approve
              </ConfirmButton>
              <ConfirmButton
                confirmLabel="Yes, not accept"
                loading={decide.mutation.isPending && decide.mutation.variables === 'reject'}
                onConfirm={() => {
                  decide.mutation.mutate('reject');
                }}
              >
                Not accept
              </ConfirmButton>
            </Group>
            <Text size="xs" c="dimmed">
              Either way they are emailed straight away.
            </Text>
          </>
        ) : null}
        {now.status === 'active' && person.may_remove ? (
          <Stack gap="xs">
            <TextInput
              label="Remove from the team"
              placeholder="Reason, for the record"
              description="Ends the membership now. They are only told that it ended."
              maxLength={2000}
              value={reason}
              error={remove.errors.reason ?? null}
              onChange={(event) => {
                setReason(event.currentTarget.value);
              }}
            />
            <Group>
              <ConfirmButton
                confirmLabel="Yes, remove"
                disabled={!reason.trim()}
                loading={remove.mutation.isPending}
                onConfirm={() => {
                  remove.mutation.mutate(undefined);
                }}
              >
                Remove
              </ConfirmButton>
            </Group>
          </Stack>
        ) : null}
      </Stack>
    </Panel>
  );
}

function Notes({ person, personKey }: { person: Person; personKey: readonly unknown[] }) {
  const slug = useSlug();
  const [body, setBody] = useState('');
  const add = useManageChange(
    personKey,
    () =>
      call(
        api.POST('/api/v1/teams/{slug}/manage/people/{user_id}/notes', {
          params: { path: { slug, user_id: person.user_id } },
          body: { body },
        }),
      ),
    'Note saved.',
  );
  return (
    <Panel title="Notes">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Seen by the leads and the admins. Not shown to the person, but part of their data if they ask for it
          (without who wrote it).
        </Text>
        {person.may_write_notes ? (
          <Stack gap="xs">
            <Textarea
              aria-label="New note"
              autosize
              minRows={3}
              maxLength={5000}
              value={body}
              error={add.errors.body ?? null}
              onChange={(event) => {
                setBody(event.currentTarget.value);
              }}
            />
            <Group>
              <Button
                variant="default"
                disabled={!body.trim()}
                loading={add.mutation.isPending}
                onClick={() => {
                  add.mutation.mutate(undefined, {
                    onSuccess: () => {
                      setBody('');
                    },
                  });
                }}
              >
                Add note
              </Button>
            </Group>
          </Stack>
        ) : null}
        {person.notes.map((note) => (
          <div key={`${note.at}-${note.body.slice(0, 20)}`}>
            <Text size="xs" c="dimmed">
              {formatDateTime(note.at)} · {note.author ?? '–'}
            </Text>
            <Text size="sm" className={classes.preLine}>
              {note.body}
            </Text>
          </div>
        ))}
      </Stack>
    </Panel>
  );
}

export function Person() {
  const slug = useSlug();
  const userId = Number(useParams().userId);
  const personKey = ['teams', slug, 'manage', 'person', userId] as const;
  const person = useQuery({
    queryKey: personKey,
    queryFn: () =>
      call(
        api.GET('/api/v1/teams/{slug}/manage/people/{user_id}', {
          params: { path: { slug, user_id: userId } },
        }),
      ),
  });
  if (person.isPending) return <LoadingState />;
  if (person.isError) return <ErrorState error={person.error} onRetry={() => void person.refetch()} />;
  const data = person.data;
  return (
    <>
      <ManageHeader title={data.name} crumbs={[{ label: 'Members', to: `/teams/${slug}/manage/members` }]} />
      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="lg">
        <Stack gap="lg">
          <Panel title="Details">
            <Details
              items={[
                ['University email', data.details.university_email],
                ['Private email', data.details.private_email],
                ['Phone', data.details.phone],
                ['Cohort', data.details.cohort],
              ]}
            />
          </Panel>
          <Now person={data} personKey={personKey} />
          <Panel title="History">
            <Stack gap="xs">
              {data.history.map((entry, index) => (
                <Group key={index} gap="xs" wrap="wrap">
                  <Pill tone={TONES[entry.status] ?? 'neutral'}>{entry.status_label}</Pill>
                  <Text size="sm" c="dimmed">
                    {entry.applied_on ? formatDate(entry.applied_on) : '–'}
                    {entry.ended_on ? ` – ${formatDate(entry.ended_on)}` : ''}
                    {entry.why ? ` · ${entry.why}` : ''}
                  </Text>
                  {entry.note ? <Text size="sm">{entry.note}</Text> : null}
                </Group>
              ))}
            </Stack>
          </Panel>
        </Stack>
        <Notes person={data} personKey={personKey} />
      </SimpleGrid>
    </>
  );
}
