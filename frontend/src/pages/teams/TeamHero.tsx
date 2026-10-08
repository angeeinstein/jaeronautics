/**
 * A team's cover: its picture (or the brand's backdrop), its logo shown whole
 * -- round, shield-shaped or square, never cut -- its name and line, and a few
 * facts: how many are in it, how to get in, what it costs, one's own place in
 * it. On its About page and, lower, on its overview (``compact``).
 */
import { Group, Stack, Text, Title } from '@mantine/core';
import { IconCoin, IconDoorEnter, IconUsers } from '@tabler/icons-react';
import { type ReactNode, useId } from 'react';

import { Pill } from '../../components/Pill';
import classes from './About.module.css';
import { StatusPill, type Team } from './shared';
import { GlowLogo } from '../../components/GlowLogo';

/** Whether somebody could get in from here: the form is there and nothing stands in the way. */
export function canJoin(team: Team) {
  return team.is_member && team.joining !== null && !team.joining.why_not;
}

export function members(count: number) {
  return count === 1 ? '1 member' : `${String(count)} members`;
}

export function TeamHero({
  team,
  compact = false,
  children,
}: {
  team: Team;
  compact?: boolean;
  /** What to do from here, under the facts. */
  children?: ReactNode;
}) {
  const joining = team.joining;
  const named = useId();
  return (
    <section className={classes.hero} data-compact={compact || undefined} aria-labelledby={named}>
      {team.picture_url ? (
        <img className={classes.cover} src={team.picture_url} alt="" data-backdrop />
      ) : (
        <div className={classes.backdrop} />
      )}
      <div className={classes.shade} />
      <Stack className={classes.heroBody} gap="md">
        <Group gap="lg" wrap="nowrap" align="flex-end">
          {team.logo_url ? (
            <div className={classes.logoTile}>
              <GlowLogo url={team.logo_url} />
            </div>
          ) : null}
          <Stack gap={6}>
            <Title order={1} className={classes.name} id={named}>
              {team.name}
            </Title>
            {team.description ? <Text className={classes.tagline}>{team.description}</Text> : null}
          </Stack>
        </Group>
        <Group gap="xs">
          <span className={classes.fact}>
            <IconUsers size={16} aria-hidden />
            {members(team.member_count)}
          </span>
          {joining && canJoin(team) ? (
            <span className={classes.fact}>
              <IconDoorEnter size={16} aria-hidden />
              {joining.mode === 'approval' ? 'Applications open' : 'Open to join'}
            </span>
          ) : null}
          <span className={classes.fact}>
            <IconCoin size={16} aria-hidden />
            {team.fee ?? 'No team fee'}
          </span>
          {team.role ? <Pill tone="info">{team.role}</Pill> : <StatusPill membership={team.membership} />}
        </Group>
        {children}
      </Stack>
    </section>
  );
}
