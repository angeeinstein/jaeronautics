/**
 * A draft compared with the version in force (docs/legal-texts-in-portal-plan.md,
 * "The editor"), for whoever approves the change: paragraph by paragraph,
 * and within a changed paragraph word by word -- what was added underlined,
 * what was removed struck through. Only the changed paragraphs are shown,
 * each with the one before and after it, unless everything is asked for.
 * Compared in the browser (the ``diff`` library), on the Markdown as written.
 */
import { Button, Text } from '@mantine/core';
import { diffArrays, diffWordsWithSpace } from 'diff';
import { useMemo, useState } from 'react';

import { plural } from '../../lib/format';
import classes from './Changes.module.css';

type Block =
  | { kind: 'same'; text: string }
  | { kind: 'added' | 'removed'; text: string }
  | { kind: 'changed'; parts: { value: string; added?: boolean; removed?: boolean }[] };

function words(text: string) {
  return text.split(/\s+/).filter(Boolean).length;
}

function compare(before: string, after: string) {
  const paragraphs = (text: string) =>
    text
      .replace(/\r\n/g, '\n')
      .trim()
      .split(/\n{2,}/);
  const chunks = diffArrays(paragraphs(before), paragraphs(after));
  const blocks: Block[] = [];
  let added = 0;
  let removed = 0;
  for (let index = 0; index < chunks.length; index++) {
    const chunk = chunks[index];
    if (!chunk) continue;
    const next = chunks[index + 1];
    if (chunk.removed && next?.added) {
      // Paragraphs replaced: compared word by word, pair by pair.
      const count = Math.max(chunk.value.length, next.value.length);
      for (let pair = 0; pair < count; pair++) {
        const old = chunk.value[pair];
        const now = next.value[pair];
        if (old !== undefined && now !== undefined) {
          const parts = diffWordsWithSpace(old, now);
          added += parts.filter((part) => part.added).reduce((sum, part) => sum + words(part.value), 0);
          removed += parts.filter((part) => part.removed).reduce((sum, part) => sum + words(part.value), 0);
          blocks.push({ kind: 'changed', parts });
        } else if (old !== undefined) {
          removed += words(old);
          blocks.push({ kind: 'removed', text: old });
        } else if (now !== undefined) {
          added += words(now);
          blocks.push({ kind: 'added', text: now });
        }
      }
      index += 1;
    } else {
      for (const text of chunk.value) {
        if (chunk.added) added += words(text);
        if (chunk.removed) removed += words(text);
        blocks.push({ kind: chunk.added ? 'added' : chunk.removed ? 'removed' : 'same', text });
      }
    }
  }
  return { blocks, added, removed };
}

export function Changes({ before, after }: { before: string; after: string }) {
  const { blocks, added, removed } = useMemo(() => compare(before, after), [before, after]);
  const [everything, setEverything] = useState(false);
  const near = (index: number) =>
    [index - 1, index, index + 1].some((at) => {
      const block = blocks[at];
      return block !== undefined && block.kind !== 'same';
    });
  const shown: (Block | { kind: 'gap'; count: number })[] = [];
  blocks.forEach((block, index) => {
    if (everything || block.kind !== 'same' || near(index)) {
      shown.push(block);
      return;
    }
    const last = shown[shown.length - 1];
    if (last?.kind === 'gap') last.count += 1;
    else shown.push({ kind: 'gap', count: 1 });
  });

  return (
    <div>
      <Text size="sm" c="dimmed" mb="sm">
        {added || removed
          ? `${String(added)} ${plural(added, 'word', 'words')} added, ${String(removed)} ${plural(removed, 'word', 'words')} removed.`
          : 'No changes.'}{' '}
        {added || removed ? (
          <Button
            variant="subtle"
            size="compact-sm"
            onClick={() => {
              setEverything((now) => !now);
            }}
          >
            {everything ? 'Only the changes' : 'The whole text'}
          </Button>
        ) : null}
      </Text>
      <div className={classes.text}>
        {shown.map((block, index) =>
          block.kind === 'gap' ? (
            <p key={index} className={classes.gap}>
              {`… ${String(block.count)} unchanged ${plural(block.count, 'paragraph', 'paragraphs')}`}
            </p>
          ) : block.kind === 'changed' ? (
            <p key={index}>
              {block.parts.map((part, at) =>
                part.added ? (
                  <ins key={at} className={classes.added}>
                    {part.value}
                  </ins>
                ) : part.removed ? (
                  <del key={at} className={classes.removed}>
                    {part.value}
                  </del>
                ) : (
                  <span key={at}>{part.value}</span>
                ),
              )}
            </p>
          ) : block.kind === 'added' ? (
            <p key={index}>
              <ins className={classes.added}>{block.text}</ins>
            </p>
          ) : block.kind === 'removed' ? (
            <p key={index}>
              <del className={classes.removed}>{block.text}</del>
            </p>
          ) : (
            <p key={index}>{block.text}</p>
          ),
        )}
      </div>
    </div>
  );
}
