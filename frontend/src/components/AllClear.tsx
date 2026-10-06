/**
 * The all-clear line (docs/design.md): where a section would only say that
 * nothing is there, one line with a still green tick, a bold sentence and a
 * muted one.
 */
import { Group, Paper, Stack, Text } from '@mantine/core';

export function AllClear({ title, detail }: { title: string; detail?: string }) {
  return (
    <Paper p="md" role="status">
      <Group gap="sm" wrap="nowrap" align="flex-start">
        <svg width="22" height="22" viewBox="0 0 26 26" aria-hidden="true">
          <path d="M4 13l6.5 6.5L22 6" fill="none" stroke="var(--ja-success-text)" strokeWidth="3" />
        </svg>
        <Stack gap={2}>
          <Text fw={600}>{title}</Text>
          {detail ? (
            <Text size="sm" c="dimmed">
              {detail}
            </Text>
          ) : null}
        </Stack>
      </Group>
    </Paper>
  );
}
