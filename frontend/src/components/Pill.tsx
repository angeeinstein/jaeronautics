/**
 * A state in one word (docs/design.md, "Status labels"): small capitals on a
 * faint tint of the state's colour. The tone says what the state means, not
 * which state it is.
 */
import { Badge } from '@mantine/core';

import classes from './Pill.module.css';

export type Tone = 'active' | 'pending' | 'failed' | 'neutral' | 'info';

const COLOURS: Record<Tone, string> = {
  active: 'green',
  pending: 'amber',
  failed: 'red',
  neutral: 'gray',
  info: 'brand',
};

export function Pill({ tone, children }: { tone: Tone; children: string }) {
  return (
    <Badge
      color={COLOURS[tone]}
      variant="light"
      radius={0}
      classNames={{ root: classes.pill, label: classes.label }}
      data-tone={tone}
    >
      {children}
    </Badge>
  );
}
