/**
 * Teams, the admins' part (docs/frontend-structure.md, 4): whether teams are
 * on for members and what they are called, then every team -- its members,
 * fee and lead -- each opening the team. Data: GET /api/v1/admin/teams
 * (aeronautics_members/api/admin_teams.py).
 */
import { Button, Card, Group, Stack, Switch, Table, Text, TextInput } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call, type Schemas } from '../../../api/client';
import { AppLink } from '../../../app/AppLink';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { TeamMark } from '../../../components/TeamMark';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { notifyDone, notifyNote } from '../../../lib/notify';
import { teamsQuery, useTeamChange } from './shared';
import classes from './Teams.module.css';

type Settings = Schemas['TeamSettings'];

function SettingsPanel({ settings }: { settings: Settings }) {
  const [value, setValue] = useState(settings);
  const save = useTeamChange((body: Settings) => call(api.PUT('/api/v1/admin/team-settings', { body })), {
    done: (answer) => {
      if (answer.changed) notifyDone('Saved.');
      else notifyNote('Nothing changed.');
    },
  });
  return (
    <Panel title="Settings">
      <Stack gap="md">
        <Switch
          label="Switched on"
          description="While off, members see nothing of teams. They can still be set up here."
          checked={value.enabled}
          onChange={(event) => {
            setValue({ ...value, enabled: event.currentTarget.checked });
          }}
        />
        <Group gap="md" align="flex-end">
          <TextInput
            label="Called (one)"
            maxLength={50}
            value={value.label_singular}
            onChange={(event) => {
              setValue({ ...value, label_singular: event.currentTarget.value });
            }}
          />
          <TextInput
            label="Called (several)"
            maxLength={50}
            value={value.label_plural}
            onChange={(event) => {
              setValue({ ...value, label_plural: event.currentTarget.value });
            }}
          />
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

export function Teams() {
  const teams = useQuery(teamsQuery);
  const plural = teams.data?.settings.label_plural ?? 'Teams';
  const singular = teams.data?.settings.label_singular ?? 'Team';

  return (
    <>
      <PageHeader
        title={plural}
        description="Create teams, appoint their leads, set their fees."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: plural }]}
        actions={
          <Button component={AppLink} to="/admin/teams/new">
            {`New ${singular}`}
          </Button>
        }
      />
      {teams.isPending ? (
        <LoadingState />
      ) : teams.isError ? (
        <ErrorState error={teams.error} onRetry={() => void teams.refetch()} />
      ) : (
        <Stack gap="lg">
          <Card padding={0} className={classes.card}>
            {teams.data.teams.length ? (
              <Table.ScrollContainer minWidth={640} type="native">
                <Table verticalSpacing="sm" highlightOnHover aria-label={plural}>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th scope="col">{singular}</Table.Th>
                      <Table.Th scope="col">Members</Table.Th>
                      <Table.Th scope="col">Fee</Table.Th>
                      <Table.Th scope="col">Lead</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {teams.data.teams.map((team) => (
                      <Table.Tr key={team.slug}>
                        <Table.Td>
                          <Group gap="sm" wrap="nowrap">
                            <TeamMark name={team.name} logoUrl={team.logo_url} />
                            <AppLink to={`/admin/teams/${team.slug}`} className={classes.name}>
                              {team.name}
                            </AppLink>
                            {team.status === 'archived' ? <Pill tone="neutral">Archived</Pill> : null}
                          </Group>
                        </Table.Td>
                        <Table.Td className="ja-figures">{team.members}</Table.Td>
                        <Table.Td>{team.fee ?? 'Free'}</Table.Td>
                        <Table.Td>
                          {team.leads.length ? team.leads.join(', ') : null}
                          {team.status === 'active' && !team.has_lead_in_force ? (
                            <Text span size="sm" c="var(--ja-warning)">
                              {team.leads.length ? ' (not in force)' : 'None'}
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
                <EmptyState>None yet.</EmptyState>
              </div>
            )}
          </Card>
          <SettingsPanel settings={teams.data.settings} />
        </Stack>
      )}
    </>
  );
}
