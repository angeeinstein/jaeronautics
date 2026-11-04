/**
 * A draft compared with the version in force, word by word
 * (docs/legal-texts-in-portal-plan.md, "The editor"): what was added
 * underlined, what was removed struck through, the rest as it was -- for
 * whoever approves the change. Compared in the browser (the ``diff``
 * library); the Markdown as written, not the page it becomes.
 */
import { Text } from '@mantine/core';
import { diffWordsWithSpace } from 'diff';
import { useMemo } from 'react';

import { plural } from '../../lib/format';
import classes from './Changes.module.css';

export function Changes({ before, after }: { before: string; after: string }) {
  const parts = useMemo(() => diffWordsWithSpace(before, after), [before, after]);
  const count = (kind: 'added' | 'removed') =>
    parts
      .filter((part) => part[kind])
      .reduce((sum, part) => sum + part.value.split(/\s+/).filter(Boolean).length, 0);
  const added = count('added');
  const removed = count('removed');
  return (
    <div>
      <Text size="sm" c="dimmed" mb="sm">
        {added || removed
          ? `${String(added)} ${plural(added, 'word', 'words')} added, ${String(removed)} ${plural(removed, 'word', 'words')} removed.`
          : 'No changes.'}
      </Text>
      <div className={classes.text}>
        {parts.map((part, index) =>
          part.added ? (
            <ins key={index} className={classes.added}>
              {part.value}
            </ins>
          ) : part.removed ? (
            <del key={index} className={classes.removed}>
              {part.value}
            </del>
          ) : (
            <span key={index}>{part.value}</span>
          ),
        )}
      </div>
    </div>
  );
}
