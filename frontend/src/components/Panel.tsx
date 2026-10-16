/**
 * A card (docs/design.md, "Cards"): the surface with a hairline border and a
 * header band with its title -- and, where it has them, actions on the right.
 */
import { Card, Group, Title } from '@mantine/core';
import type { IconProps } from '@tabler/icons-react';
import type { ComponentType, ReactNode } from 'react';

import classes from './Panel.module.css';

interface PanelProps {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  /** No padding around the body, for lists that run to the edges. */
  flush?: boolean;
  /** Beside the title, as its page's entry in the sidebar has: found by its symbol at a glance. */
  icon?: ComponentType<IconProps>;
}

export function Panel({ title, actions, children, flush = false, icon: Icon }: PanelProps) {
  return (
    <Card padding={0} className={classes.panel} component="section" aria-label={title}>
      <Group className={classes.header} justify="space-between" gap="sm" wrap="nowrap">
        <Group gap="xs" wrap="nowrap">
          {Icon ? <Icon size={20} className={classes.icon} aria-hidden /> : null}
          <Title order={2} className={classes.title}>
            {title}
          </Title>
        </Group>
        {actions ? <Group gap="xs">{actions}</Group> : null}
      </Group>
      <div className={flush ? undefined : classes.body}>{children}</div>
    </Card>
  );
}
