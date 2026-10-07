/**
 * Teams: first the ones somebody is in, applying to or helps run -- each a
 * row with its state and what to do next -- then the others as cards, with
 * what they are about. Data: GET /api/v1/teams (?paid= when back from paying).
 */
import { Alert, Anchor, Button, Card, Group, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';

import { api, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { PageHeader } from '../../components/PageHeader';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { TeamMark } from '../../components/TeamMark';
import { MembershipBlock, StatusPill, teamsKey } from './shared';
import classes from './Teams.module.css';

type Card = Schemas['TeamCardOut'];

function nameLink(card: Card) {
  return card.opens === 'team' ? `/teams/${card.slug}` : `/teams/${card.slug}/about`;
}

function Mine({ card }: { card: Card }) {
  return (
    <Card withBorder padding="md" component="section" aria-label={card.name}>
      <div className={classes.row}>
        <Group gap="sm" wrap="nowrap" align="flex-start">
          <TeamMark name={card.name} logoUrl={card.logo_url} />
          <Stack gap={4}>
            <Anchor component={AppLink} to={nameLink(card)} fw={600}>
              {card.name}
            </Anchor>
            <div>
              <StatusPill membership={card.membership} />
            </div>
          </Stack>
        </Group>
        <MembershipBlock slug={card.slug} membership={card.membership} />
      </div>
    </Card>
  );
}

function Other({ card }: { card: Card }) {
  return (
    <Card withBorder padding="md" component="section" aria-label={card.name}>
      <Stack gap="sm" h="100%">
        <Group gap="sm" wrap="nowrap">
          <TeamMark name={card.name} logoUrl={card.logo_url} />
          <Stack gap={4}>
            <Anchor component={AppLink} to={nameLink(card)} fw={600}>
              {card.name}
            </Anchor>
            <div>
              <StatusPill membership={card.membership} />
            </div>
          </Stack>
        </Group>
        {card.description ? <Text size="sm">{card.description}</Text> : null}
        {card.membership.fee ? <Text size="sm" c="dimmed">{`Fee: ${card.membership.fee}.`}</Text> : null}
        <Group mt="auto">
          <Button component={AppLink} to={`/teams/${card.slug}/about`}>
            {card.about_label}
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

export function Teams() {
  const [search] = useSearchParams();
  const paid = search.get('paid');
  const home = useQuery({
    queryKey: [...teamsKey, 'home', paid] as const,
    queryFn: () => call(api.GET('/api/v1/teams', { params: { query: paid ? { paid } : {} } })),
  });
  const labels = home.data?.labels;
  return (
    <>
      <PageHeader
        title={labels?.plural ?? 'Teams'}
        description="Groups inside the association with their own members."
      />
      {home.isPending ? (
        <LoadingState />
      ) : home.isError ? (
        <ErrorState error={home.error} onRetry={() => void home.refetch()} />
      ) : (
        <Stack gap="xl">
          {!home.data.is_member ? (
            <Alert color="brand" variant="light">
              Teams are for members of the association.
            </Alert>
          ) : null}
          {home.data.mine.length ? (
            <Stack gap="sm" component="section" aria-label={`My ${home.data.labels.plural.toLowerCase()}`}>
              <Title order={2} size="h4">
                {`My ${home.data.labels.plural.toLowerCase()}`}
              </Title>
              {home.data.mine.map((card) => (
                <Mine key={card.slug} card={card} />
              ))}
            </Stack>
          ) : null}
          <Stack gap="sm" component="section" aria-label={`Other ${home.data.labels.plural.toLowerCase()}`}>
            {home.data.mine.length ? (
              <Title order={2} size="h4">
                {`Other ${home.data.labels.plural.toLowerCase()}`}
              </Title>
            ) : null}
            {home.data.others.length ? (
              <div className={classes.cards}>
                {home.data.others.map((card) => (
                  <Other key={card.slug} card={card} />
                ))}
              </div>
            ) : (
              <EmptyState>{home.data.mine.length ? 'None.' : 'None yet.'}</EmptyState>
            )}
          </Stack>
        </Stack>
      )}
    </>
  );
}
