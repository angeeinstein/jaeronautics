/**
 * One team, the admins' part (docs/frontend-structure.md, 4): its details,
 * its fee, its roles, and archiving it -- each a card with its own save.
 * "Team page and settings" leads to the team's own management, which site
 * admins can open too. Data: GET /api/v1/admin/teams/<slug>.
 */
import { Alert, Button, Group, Select, Stack, Table, Text, TextInput, VisuallyHidden } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router';

import { api, ApiError, call } from '../../../api/client';
import { AppLink } from '../../../app/AppLink';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Details as Facts } from '../../../components/Details';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { notifyDone, notifyNote } from '../../../lib/notify';
import { feeChangeNotes, teamQuery, teamsQuery, type TeamOut, useTeamChange } from './shared';
import { type Details, DetailsFields, type FeeFields, FeeFieldsEditor } from './TeamFields';

function DetailsPanel({ team }: { team: TeamOut }) {
  const [value, setValue] = useState<Details>({
    name: team.name,
    admission_mode: team.admission_mode,
    max_members: team.max_members,
    forum_group: team.forum_group,
    access_list_enabled: team.access_list_enabled,
  });
  const save = useTeamChange(
    (body: Details) =>
      call(api.PUT('/api/v1/admin/teams/{slug}', { params: { path: { slug: team.slug } }, body })),
    {
      done: () => {
        notifyDone('Saved.');
      },
    },
  );
  return (
    <Panel title="Details">
      <Stack gap="md">
        <DetailsFields value={value} onChange={setValue} />
        <Text size="sm" c="dimmed">
          Short name: {team.slug} (used in links, cannot be changed).
        </Text>
        <Group justify="flex-end">
          <Button
            loading={save.isPending}
            onClick={() => {
              save.mutate(value);
            }}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function FeePanel({ team }: { team: TeamOut }) {
  const [value, setValue] = useState<FeeFields>({
    payment_mode: team.fee.payment_mode,
    stripe_price_id: team.fee.stripe_price_id,
    period_starts: team.fee.period_starts,
  });
  const save = useTeamChange(
    (body: FeeFields) =>
      call(api.PUT('/api/v1/admin/teams/{slug}/fee', { params: { path: { slug: team.slug } }, body })),
    {
      done: (change) => {
        notifyDone('Fee saved.');
        for (const note of feeChangeNotes(change)) notifyNote(note);
      },
    },
  );
  return (
    <Panel title="Fee">
      <Stack gap="md">
        <Facts items={[['Now', team.fee.display ?? 'Free']]} />
        <FeeFieldsEditor value={value} onChange={setValue} />
        <Text size="sm" c="dimmed">
          Checked with Stripe before it is saved. Members already in are moved to the new fee or asked to pay,
          and emailed; that cannot be taken back once they are.
        </Text>
        <Group justify="flex-end">
          <ConfirmButton
            color="brand"
            confirmLabel="Yes, change the fee"
            loading={save.isPending}
            onConfirm={() => {
              save.mutate(value);
            }}
          >
            Save fee
          </ConfirmButton>
        </Group>
      </Stack>
    </Panel>
  );
}

const ROLE_CHOICES = [
  { value: 'lead', label: 'Lead' },
  { value: 'treasurer', label: 'Treasurer' },
];

function RolesPanel({ team }: { team: TeamOut }) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<'lead' | 'treasurer'>('lead');
  const leads = team.roles.filter((holder) => holder.role === 'lead').length;
  const grant = useTeamChange(
    () =>
      call(
        api.POST('/api/v1/admin/teams/{slug}/roles', {
          params: { path: { slug: team.slug } },
          body: { email: email.trim(), role },
        }),
      ),
    {
      done: () => {
        setEmail('');
        notifyDone('Role given.');
      },
    },
  );
  const revoke = useTeamChange(
    (body: { user_id: number; role: 'lead' | 'treasurer'; confirmed: boolean }) =>
      call(
        api.POST('/api/v1/admin/teams/{slug}/roles/revoke', { params: { path: { slug: team.slug } }, body }),
      ),
    {
      done: () => {
        notifyDone('Role removed.');
      },
    },
  );

  return (
    <Panel title="Roles">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          A role counts only while its holder is a member of the association and of this team.
        </Text>
        {team.roles.length ? (
          <Table.ScrollContainer minWidth={560} type="native">
            <Table verticalSpacing="sm" aria-label="Roles">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Person</Table.Th>
                  <Table.Th scope="col">Role</Table.Th>
                  <Table.Th scope="col">In force</Table.Th>
                  <Table.Th scope="col">
                    <VisuallyHidden>Action</VisuallyHidden>
                  </Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {team.roles.map((holder) => {
                  const lastLead = holder.role === 'lead' && leads <= 1;
                  const remove = () => {
                    revoke.mutate({ user_id: holder.user_id, role: holder.role, confirmed: lastLead });
                  };
                  return (
                    <Table.Tr key={`${String(holder.user_id)}-${holder.role}`}>
                      <Table.Td>
                        {holder.name}
                        {holder.name !== holder.email ? (
                          <Text size="sm" c="dimmed">
                            {holder.email}
                          </Text>
                        ) : null}
                      </Table.Td>
                      <Table.Td>{holder.role_label}</Table.Td>
                      <Table.Td>
                        {holder.in_force ? (
                          <Pill tone="active">Yes</Pill>
                        ) : (
                          <Pill tone="pending">Not in the team, or lapsed</Pill>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <ConfirmButton
                          size="xs"
                          confirmLabel={lastLead ? 'Yes, remove the last lead' : 'Yes, remove'}
                          onConfirm={remove}
                        >
                          Remove
                        </ConfirmButton>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <EmptyState>Nobody yet.</EmptyState>
        )}
        <Group align="flex-end" gap="sm">
          <TextInput
            type="email"
            label="Email address"
            description="The address they sign in with."
            value={email}
            onChange={(event) => {
              setEmail(event.currentTarget.value);
            }}
            flex="1 1 16rem"
          />
          <Select
            label="Role"
            data={ROLE_CHOICES}
            allowDeselect={false}
            value={role}
            onChange={(next) => {
              setRole(next === 'treasurer' ? 'treasurer' : 'lead');
            }}
            w={160}
          />
          <Button
            loading={grant.isPending}
            disabled={!email.trim()}
            onClick={() => {
              grant.mutate(undefined);
            }}
          >
            Give role
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function ArchivePanel({ team }: { team: TeamOut }) {
  const [typed, setTyped] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const change = useTeamChange(
    (archived: boolean) =>
      call(
        api.PUT('/api/v1/admin/teams/{slug}/archived', {
          params: { path: { slug: team.slug } },
          body: { archived, confirm_name: archived ? typed : null },
        }),
      ),
    {
      done: (answer) => {
        setTyped('');
        notifyDone(answer.status === 'archived' ? 'Archived.' : 'Restored.');
      },
      onError: (error) => {
        if (error instanceof ApiError && error.status === 400) {
          setProblem(error.message);
          return true;
        }
        return false;
      },
    },
  );

  if (team.status === 'archived') {
    return (
      <Panel title="Archived">
        <Group justify="space-between" gap="sm">
          <Text size="sm" c="dimmed">
            Nobody can join, and leads cannot open it. Restoring makes it available again.
          </Text>
          <Button
            variant="default"
            loading={change.isPending}
            onClick={() => {
              change.mutate(false);
            }}
          >
            Restore
          </Button>
        </Group>
      </Panel>
    );
  }
  return (
    <Panel title="Archive">
      <Stack gap="sm">
        <Text size="sm">
          Its members lose the team at once: its pages, its forum group and the access list. Nothing is
          deleted, and it can be restored. While anybody still pays for it by subscription, it cannot be
          archived; make it free first.
        </Text>
        <Group align="flex-end" gap="sm">
          <TextInput
            label={`Type the team's name, ${team.name}, to confirm`}
            autoComplete="off"
            value={typed}
            error={problem}
            onChange={(event) => {
              setTyped(event.currentTarget.value);
              setProblem(null);
            }}
            flex="1 1 18rem"
          />
          <Button
            color="red"
            variant="outline"
            loading={change.isPending}
            disabled={typed.trim() !== team.name}
            onClick={() => {
              change.mutate(true);
            }}
          >
            Archive
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

export function Team() {
  const { slug = '' } = useParams();
  const team = useQuery(teamQuery(slug));
  const teams = useQuery(teamsQuery);
  const plural = teams.data?.settings.label_plural ?? 'Teams';
  const title = team.data?.name ?? slug;

  return (
    <>
      <PageHeader
        title={title}
        description="The association's part of the team."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: plural, to: '/admin/teams' }, { label: title }]}
        actions={
          team.data ? (
            <Button component={AppLink} to={team.data.manage_url} variant="default">
              Team page and settings
            </Button>
          ) : null
        }
      />
      {team.isPending ? (
        <LoadingState />
      ) : team.isError ? (
        <ErrorState error={team.error} onRetry={() => void team.refetch()} />
      ) : (
        // Keyed by what was saved, so each card starts afresh from it.
        <Stack gap="lg" key={JSON.stringify(team.data)}>
          {team.data.status === 'archived' ? (
            <Alert variant="light">Archived. Nobody can join, and leads cannot open it.</Alert>
          ) : !team.data.has_lead_in_force ? (
            <Alert color="amber" variant="light">
              No lead in force. Until there is one, only admins can run it.
            </Alert>
          ) : null}
          <DetailsPanel team={team.data} />
          <FeePanel team={team.data} />
          <RolesPanel team={team.data} />
          <ArchivePanel team={team.data} />
        </Stack>
      )}
    </>
  );
}
