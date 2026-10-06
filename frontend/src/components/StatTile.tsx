/**
 * A figure (docs/design.md, "Figures"): the number in the mono face, what it
 * counts, and the one number that explains it. A tile with a page behind it
 * is a link to that page.
 */
import { Paper, Text, UnstyledButton } from '@mantine/core';

import { AppLink } from '../app/AppLink';
import classes from './StatTile.module.css';

interface StatTileProps {
  value: string | number;
  label: string;
  note?: string;
  to?: string;
}

export function StatTile({ value, label, note, to }: StatTileProps) {
  const body = (
    <Paper className={classes.tile} p="md" data-link={to ? true : undefined}>
      <Text className={`${classes.value} ja-figures`}>{value}</Text>
      <Text fw={500}>{label}</Text>
      {note ? (
        <Text size="sm" c="dimmed" mt={2}>
          {note}
        </Text>
      ) : null}
    </Paper>
  );
  if (!to) return body;
  return (
    <UnstyledButton component={AppLink} to={to} className={classes.link}>
      {body}
    </UnstyledButton>
  );
}
