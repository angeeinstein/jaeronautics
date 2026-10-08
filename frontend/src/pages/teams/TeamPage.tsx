/**
 * A team's overview, for its members and whoever runs it, in the team's own
 * frame: its cover; what needs doing (for whoever runs it), one's own
 * membership with what to do about it -- leaving set apart, quietly -- and
 * who is in the team, each by their picture. Anybody else is sent to what the
 * team is about. Asked again every minute, so somebody who joins appears.
 * Data: GET /api/v1/teams/<slug>.
 */
import { Anchor, Avatar, SimpleGrid, Stack, Text } from '@mantine/core';
import { IconChevronRight } from '@tabler/icons-react';
import { Navigate, useParams } from 'react-router';

import { AppLink } from '../../app/AppLink';
import { Breadcrumbs, useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { Pill } from '../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { useArrivals } from '../../lib/arrivals';
import { useLiveRefresh } from '../../lib/live';
import { RulesLine } from './Rules';
import { MembershipBlock, type Team, teamQuery, useTeam } from './shared';
import { TeamHero } from './TeamHero';
import classes from './Teams.module.css';

type Person = NonNullable<Team['members']>[number];

const personKey = (person: Person) => `${person.name}|${person.university_email ?? ''}`;

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join('');
}

/** For whoever runs the team: what waits for them, each a way straight there. */
function Attention({ team }: { team: Team }) {
  const waiting = team.applications_waiting ?? 0;
  if (!waiting) return null;
  return (
    <Panel title="Needs your attention">
      <AppLink to={`/teams/${team.slug}/manage`} className={classes.todo}>
        <span className={classes.todoCount}>{waiting}</span>
        <span className={classes.todoText}>
          {waiting === 1 ? 'Application to answer' : 'Applications to answer'}
        </span>
        <IconChevronRight size={18} aria-hidden />
      </AppLink>
    </Panel>
  );
}

function Members({ team }: { team: Team }) {
  const people = team.members ?? [];
  const arrived = useArrivals(team.slug, team.members?.map(personKey));
  return (
    <Panel
      title="Members"
      actions={
        <Text size="sm" c="dimmed">
          {people.length}
        </Text>
      }
    >
      {people.length ? (
        <ul className={classes.roster} aria-label="Members">
          {people.map((person) => (
            <li
              key={personKey(person)}
              className={`${classes.person} ${arrived.has(personKey(person)) ? 'ja-arrived-card' : ''}`}
            >
              <Avatar src={person.picture_url} alt="" radius={0} size={48} aria-hidden>
                {initials(person.name)}
              </Avatar>
              <Stack gap={2} miw={0}>
                <Text size="sm" fw={500} truncate>
                  {person.name}
                </Text>
                {person.is_lead ? (
                  <div>
                    <Pill tone="info">Lead</Pill>
                  </div>
                ) : null}
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
  );
}

export function TeamPage() {
  const { slug = '' } = useParams();
  const team = useTeam(slug);
  useDocumentTitle(team.data?.name ?? 'Team');
  useLiveRefresh(teamQuery(slug).queryKey, 60_000, team.data?.sees_team_page ?? false);
  if (team.isPending) return <LoadingState />;
  if (team.isError) return <ErrorState error={team.error} onRetry={() => void team.refetch()} />;
  const data = team.data;
  if (!data.sees_team_page) return <Navigate to={`/teams/${slug}/about`} replace />;
  const attention = Boolean(data.applications_waiting);
  return (
    <Stack gap="lg">
      <Breadcrumbs crumbs={[{ label: data.labels.plural, to: '/teams' }, { label: data.name }]} />
      <TeamHero team={data} compact />
      <SimpleGrid cols={{ base: 1, md: attention ? 2 : 1 }} spacing="lg">
        <Attention team={data} />
        <Panel title="Your membership">
          <Stack gap="md">
            <MembershipBlock slug={slug} membership={data.membership} onTeamPage />
            {data.rules && data.membership.ongoing ? (
              <RulesLine slug={slug} name={data.name} rules={data.rules} />
            ) : null}
          </Stack>
        </Panel>
      </SimpleGrid>
      <Members team={data} />
    </Stack>
  );
}
