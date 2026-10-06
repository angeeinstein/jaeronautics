/**
 * A card (docs/design.md, "Cards"): the surface with a hairline border and a
 * header band with its title -- and, where it has them, actions on the right.
 */
import { Card, Group, Title } from '@mantine/core';
import type { ReactNode } from 'react';

import classes from './Panel.module.css';

interface PanelProps {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  /** No padding around the body, for lists that run to the edges. */
  flush?: boolean;
}

export function Panel({ title, actions, children, flush = false }: PanelProps) {
  return (
    <Card padding={0} className={classes.panel} component="section" aria-label={title}>
      <Group className={classes.header} justify="space-between" gap="sm" wrap="nowrap">
        <Title order={2} className={classes.title}>
          {title}
        </Title>
        {actions ? <Group gap="xs">{actions}</Group> : null}
      </Group>
      <div className={flush ? undefined : classes.body}>{children}</div>
    </Card>
  );
}
