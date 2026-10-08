/**
 * A figure (docs/design.md, "Figures"): the number in the mono face, what it
 * counts, and the one number that explains it. A tile with a page behind it
 * is a link to that page. A figure that changes while the page is open
 * (lib/live.ts) lights up for a moment.
 */
import { Paper, Text, UnstyledButton } from '@mantine/core';
import { useEffect, useRef, useState } from 'react';

import { AppLink } from '../app/AppLink';
import classes from './StatTile.module.css';

interface StatTileProps {
  value: string | number;
  label: string;
  note?: string;
  to?: string;
}

/** Counts the changes of ``value`` after the first: a new key replays the highlight. */
function useChanges(value: string | number): number {
  const last = useRef(value);
  const [changes, setChanges] = useState(0);
  useEffect(() => {
    if (last.current !== value) {
      last.current = value;
      setChanges((count) => count + 1);
    }
  }, [value]);
  return changes;
}

export function StatTile({ value, label, note, to }: StatTileProps) {
  const changes = useChanges(value);
  const body = (
    <Paper className={classes.tile} p="md" data-link={to ? true : undefined}>
      <Text key={changes} className={`${classes.value} ja-figures${changes ? ' ja-changed' : ''}`}>
        {value}
      </Text>
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
