/**
 * What a log entry recorded -- the state before, after, and its details --
 * as the JSON it is, one labelled block each; nothing for what is empty.
 */
import { Code, Stack, Text } from '@mantine/core';

function Block({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined) return null;
  return (
    <Stack gap={2}>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Code block>{JSON.stringify(value, null, 2)}</Code>
    </Stack>
  );
}

export function hasPayload(entry: { before?: unknown; after?: unknown; details?: unknown }): boolean {
  return entry.before != null || entry.after != null || entry.details != null;
}

export function AuditPayload({
  before,
  after,
  details,
}: {
  before?: unknown;
  after?: unknown;
  details?: unknown;
}) {
  return (
    <Stack gap="xs">
      <Block label="Before" value={before} />
      <Block label="After" value={after} />
      <Block label="Details" value={details} />
    </Stack>
  );
}
