/**
 * Lines that each come to an answer (docs/design.md, "Checklists"): a tick, a
 * cross, a spinner while it is being worked out, a dot for what comes later --
 * and under each line what it found. The state is also said in words, for
 * whoever cannot see the icon.
 */
import { Loader, Text, VisuallyHidden } from '@mantine/core';
import { IconCheck, IconClock, IconPoint, IconX } from '@tabler/icons-react';

import classes from './Checklist.module.css';

export type CheckState = 'ok' | 'failed' | 'running' | 'waiting' | 'scheduled' | 'pending' | 'skipped';

export interface CheckLine {
  key: string;
  label: string;
  state: CheckState;
  detail?: string | null;
}

const SAID: Record<CheckState, string> = {
  ok: 'Done',
  failed: 'Failed',
  running: 'In progress',
  waiting: 'Waiting',
  scheduled: 'Later',
  pending: 'Not started',
  skipped: 'Skipped',
};

function Mark({ state }: { state: CheckState }) {
  if (state === 'ok') return <IconCheck size={18} color="var(--ja-success-text)" aria-hidden />;
  if (state === 'failed') return <IconX size={18} color="var(--ja-danger)" aria-hidden />;
  if (state === 'running' || state === 'waiting') return <Loader size={14} aria-hidden />;
  if (state === 'scheduled') return <IconClock size={16} color="var(--ja-muted)" aria-hidden />;
  return <IconPoint size={18} color="var(--ja-muted)" aria-hidden />;
}

export function Checklist({ label, items }: { label: string; items: CheckLine[] }) {
  return (
    <ul className={classes.list} aria-label={label}>
      {items.map((item) => (
        <li key={item.key} className={classes.line} data-state={item.state}>
          <span className={classes.mark}>
            <Mark state={item.state} />
          </span>
          <span>
            <Text size="sm" span className={classes.label}>
              {item.label}
              <VisuallyHidden>{`: ${SAID[item.state]}`}</VisuallyHidden>
            </Text>
            {item.detail ? (
              <Text
                size="xs"
                c={item.state === 'failed' ? 'var(--ja-danger)' : 'dimmed'}
                className={classes.detail}
              >
                {item.detail}
              </Text>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
}
