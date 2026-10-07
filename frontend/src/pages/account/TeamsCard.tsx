/**
 * My Account › Teams: the teams one is in, applying to or helps run, each
 * with its state -- or, for a member in none, what teams are.
 */
import { Button, Group, Stack, Text } from '@mantine/core';

import type { Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { Panel } from '../../components/Panel';
import { TeamMark } from '../../components/TeamMark';

type Teams = Schemas['TeamsCardOut'];

export function TeamsCard({ teams }: { teams: Teams }) {
  return (
    <Panel
      title={teams.plural}
      actions={
        teams.mine.length ? (
          <Button component={AppLink} to="/teams" size="xs" variant="default">
            {`All ${teams.plural.toLowerCase()}`}
          </Button>
        ) : null
      }
    >
      {teams.mine.length ? (
        <Stack gap="sm">
          {teams.mine.map((team) => (
            <Group key={team.slug} justify="space-between" gap="sm" wrap="nowrap">
              <Group gap="sm" wrap="nowrap">
                <TeamMark name={team.name} logoUrl={team.logo_url} />
                <AppLink to={team.opens === 'team' ? `/teams/${team.slug}` : `/teams/${team.slug}/about`}>
                  {team.name}
                </AppLink>
              </Group>
              {team.status_label ? (
                <Text size="sm" c="dimmed">
                  {team.status_label}
                </Text>
              ) : null}
            </Group>
          ))}
        </Stack>
      ) : (
        <Stack gap="sm">
          <Text size="sm">
            Groups inside the association with their own members. See which there are, and join or apply.
          </Text>
          <Group>
            <Button component={AppLink} to="/teams">
              {`See the ${teams.plural}`}
            </Button>
          </Group>
        </Stack>
      )}
    </Panel>
  );
}
