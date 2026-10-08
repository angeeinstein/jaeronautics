/**
 * A team's admin settings, in its own side menu for site admins only: its
 * details (name, joining, places, forum group, access list), its fee, and
 * archiving it. They used to be a page of their own under Admin › Teams,
 * which now only lists the teams and creates new ones -- an admin runs a team
 * from the team's own pages, like its leads, with these besides
 * (docs/teams-plan.md). Data: GET|PUT /api/v1/admin/teams/<slug>, /fee,
 * /archived (aeronautics_members/api/admin_teams.py).
 */
import { Alert, Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Details as Facts } from '../../../components/Details';
import { Panel } from '../../../components/Panel';
import { ErrorState, LoadingState } from '../../../components/States';
import { notifyDone, notifyFailed, notifyNote } from '../../../lib/notify';
import { feeChangeNotes, teamQuery, type TeamOut } from '../../admin/teams/shared';
import { type Details, DetailsFields, type FeeFields, FeeFieldsEditor } from '../../admin/teams/TeamFields';
import { ManageHeader, useSlug } from './shared';

/** A change to the team: everything that shows it is fetched again -- its pages, the admins' list, the menus. */
function useAdminChange<Input, Output>(
  run: (input: Input) => Promise<Output>,
  { done, onError }: { done: (output: Output) => void; onError?: (error: unknown) => boolean },
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async (output) => {
      done(output);
      await Promise.all([
        client.invalidateQueries({ queryKey: ['admin', 'teams'] }),
        client.invalidateQueries({ queryKey: ['teams'] }),
        client.invalidateQueries({ queryKey: ['me'] }),
      ]);
    },
    onError: (error) => {
      if (!onError?.(error)) notifyFailed(error);
    },
  });
}

function WithTeam({ children }: { children: (team: TeamOut) => React.ReactNode }) {
  const team = useQuery(teamQuery(useSlug()));
  if (team.isPending) return <LoadingState />;
  if (team.isError) return <ErrorState error={team.error} onRetry={() => void team.refetch()} />;
  // Keyed by what was saved, so each form starts afresh from it.
  return <div key={JSON.stringify(team.data)}>{children(team.data)}</div>;
}

const ADMIN_ONLY = 'Only site admins see this.';

function DetailsForm({ team }: { team: TeamOut }) {
  const [value, setValue] = useState<Details>({ name: team.name, forum_group: team.forum_group });
  const save = useAdminChange(
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

export function DetailsPage() {
  return (
    <>
      <ManageHeader title="Details" description={`The team's name and its forum group. ${ADMIN_ONLY}`} />
      <WithTeam>{(team) => <DetailsForm team={team} />}</WithTeam>
    </>
  );
}

function FeeForm({ team }: { team: TeamOut }) {
  const [value, setValue] = useState<FeeFields>({
    payment_mode: team.fee.payment_mode,
    stripe_price_id: team.fee.stripe_price_id,
    period_starts: team.fee.period_starts,
  });
  const save = useAdminChange(
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

export function FeePage() {
  return (
    <>
      <ManageHeader
        title="Fee"
        description={`What members pay for the team, through Stripe. ${ADMIN_ONLY}`}
      />
      <WithTeam>{(team) => <FeeForm team={team} />}</WithTeam>
    </>
  );
}

function ArchiveForm({ team }: { team: TeamOut }) {
  const [typed, setTyped] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const change = useAdminChange(
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

export function ArchivePage() {
  return (
    <>
      <ManageHeader title="Archive" description={`Closing the team, or opening it again. ${ADMIN_ONLY}`} />
      <WithTeam>
        {(team) => (
          <Stack gap="lg">
            {team.status === 'archived' ? (
              <Alert variant="light">Archived. Nobody can join, and leads cannot open it.</Alert>
            ) : null}
            <ArchiveForm team={team} />
          </Stack>
        )}
      </WithTeam>
    </>
  );
}
