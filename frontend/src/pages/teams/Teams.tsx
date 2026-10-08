/**
 * Teams: first the ones somebody is in, applying to or helps run, then the
 * others -- each a tile, the whole of it the way into the team: its cover,
 * its logo, its name and line, how many are in it, and the one thing that
 * matters now (applications to answer, a fee due, applications open). What
 * to do is on the team's own pages. Data: GET /api/v1/teams (?paid= when back
 * from paying).
 */
import { Alert, Stack, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useId } from 'react';
import { useSearchParams } from 'react-router';

import { api, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { PageHeader } from '../../components/PageHeader';
import { Pill } from '../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import classes from './Overview.module.css';
import { StatusPill, teamsKey } from './shared';
import { members } from './TeamHero';

type Card = Schemas['TeamCardOut'];

function target(card: Card) {
  return card.opens === 'team' ? `/teams/${card.slug}` : `/teams/${card.slug}/about`;
}

type Tone = 'warning' | 'accent' | 'plain';

/** The one thing that matters now about this team, for this person. */
function now(card: Card, mine: boolean): { text: string; tone: Tone }[] {
  if (mine) {
    const waiting = card.applications_waiting ?? 0;
    if (waiting)
      return [
        {
          text: waiting === 1 ? '1 application to answer' : `${String(waiting)} applications to answer`,
          tone: 'warning',
        },
      ];
    const note = card.membership.notes[0];
    if (note)
      return [
        {
          text: note.text,
          tone: note.tone === 'warning' ? 'warning' : note.tone === 'info' ? 'accent' : 'plain',
        },
      ];
    return [];
  }
  const said: { text: string; tone: Tone }[] = [];
  if (card.admission)
    said.push({ text: card.admission === 'approval' ? 'Applications open' : 'Open to join', tone: 'accent' });
  said.push({ text: card.membership.fee ?? 'No fee', tone: 'plain' });
  return said;
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter((word) => /^[A-Za-z0-9]/.test(word))
    .slice(0, 2)
    .map((word) => word.charAt(0).toUpperCase())
    .join('');
}

function Tile({ card, mine }: { card: Card; mine: boolean }) {
  const id = useId();
  return (
    <li>
      {/* Named for the team; its state, line and facts are what it says besides. */}
      <AppLink
        to={target(card)}
        className={classes.tile}
        aria-labelledby={`${id}-name`}
        aria-describedby={`${id}-badge ${id}-line ${id}-facts`}
      >
        <span className={classes.cover} data-picture={card.picture_url ? true : undefined}>
          {card.picture_url ? (
            <img className={classes.coverImage} src={card.picture_url} alt="" data-backdrop />
          ) : null}
          <span className={classes.badge} id={`${id}-badge`}>
            {card.role ? <Pill tone="info">{card.role}</Pill> : <StatusPill membership={card.membership} />}
          </span>
          <span className={classes.logo} aria-hidden>
            {card.logo_url ? <img src={card.logo_url} alt="" /> : initials(card.name)}
          </span>
        </span>
        <span className={classes.body}>
          <span className={classes.name} id={`${id}-name`}>
            {card.name}
          </span>
          {card.description ? (
            <span className={classes.line} id={`${id}-line`}>
              {card.description}
            </span>
          ) : null}
          <span className={classes.facts} id={`${id}-facts`}>
            <span>{members(card.member_count)}</span>
            {now(card, mine).map((item) => (
              <span key={item.text} data-tone={item.tone}>
                {item.text}
              </span>
            ))}
          </span>
        </span>
      </AppLink>
    </li>
  );
}

function Tiles({
  label,
  title,
  cards,
  mine,
}: {
  label: string;
  title: string | null;
  cards: Card[];
  mine: boolean;
}) {
  return (
    <Stack gap="md" component="section" aria-label={label}>
      {title ? (
        <Title order={2} size="h4">
          {title}
        </Title>
      ) : null}
      {cards.length ? (
        <ul className={classes.grid}>
          {cards.map((card) => (
            <Tile key={card.slug} card={card} mine={mine} />
          ))}
        </ul>
      ) : (
        <EmptyState>None yet.</EmptyState>
      )}
    </Stack>
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
            <Tiles
              label={`My ${home.data.labels.plural.toLowerCase()}`}
              title={`My ${home.data.labels.plural.toLowerCase()}`}
              cards={home.data.mine}
              mine
            />
          ) : null}
          {home.data.others.length || !home.data.mine.length ? (
            <Tiles
              label={`Other ${home.data.labels.plural.toLowerCase()}`}
              title={home.data.mine.length ? `Other ${home.data.labels.plural.toLowerCase()}` : null}
              cards={home.data.others}
              mine={false}
            />
          ) : null}
        </Stack>
      )}
    </>
  );
}
