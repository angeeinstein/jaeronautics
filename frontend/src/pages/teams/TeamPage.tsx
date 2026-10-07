/**
 * A team's own page, for its members (and whoever manages it): their
 * membership and who is in the team. Anybody else is sent to what the team
 * is about. Built as cards, so more can be added -- documents, dates --
 * without reworking it. Data: GET /api/v1/teams/<slug>.
 */
import { Avatar, Anchor, Button, Stack, Text } from '@mantine/core';
import { Navigate, useParams } from 'react-router';

import { AppLink } from '../../app/AppLink';
import { Panel } from '../../components/Panel';
import { Pill } from '../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { RulesLine } from './Rules';
import { MembershipBlock, TeamTitle, useTeam } from './shared';
import classes from './Teams.module.css';

export function TeamPage() {
  const { slug = '' } = useParams();
  const team = useTeam(slug);
  if (team.isPending) return <LoadingState />;
  if (team.isError) return <ErrorState error={team.error} onRetry={() => void team.refetch()} />;
  const data = team.data;
  if (!data.sees_team_page) return <Navigate to={`/teams/${slug}/about`} replace />;
  return (
    <>
      <TeamTitle team={data}>
        <Button component={AppLink} to={`/teams/${slug}/about`} variant="default">
          About the team
        </Button>
      </TeamTitle>
      <Stack gap="lg">
        <Panel title="Your membership">
          <Stack gap="md">
            <MembershipBlock slug={slug} membership={data.membership} onTeamPage />
            {data.rules && data.membership.ongoing ? (
              <RulesLine slug={slug} name={data.name} rules={data.rules} />
            ) : null}
          </Stack>
        </Panel>
        <Panel
          title="Members"
          actions={
            <Text size="sm" c="dimmed">
              {data.members?.length ?? 0}
            </Text>
          }
        >
          {data.members?.length ? (
            <ul className={classes.roster} aria-label="Members">
              {data.members.map((person) => (
                <li key={`${person.name}-${person.university_email ?? ''}`} className={classes.person}>
                  <Avatar src={person.picture_url} alt="" radius={0} size={44} aria-hidden>
                    {person.name.charAt(0)}
                  </Avatar>
                  <Stack gap={0} miw={0}>
                    <Text size="sm">
                      {person.name} {person.is_lead ? <Pill tone="info">Lead</Pill> : null}
                    </Text>
                    {person.university_email ? (
                      <Anchor href={`mailto:${person.university_email}`} size="xs" truncate>
                        {person.university_email}
                      </Anchor>
                    ) : null}
                  </Stack>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>Nobody yet.</EmptyState>
          )}
        </Panel>
      </Stack>
    </>
  );
}
