/**
 * What a page or a card shows while its data is not there yet, when there is
 * none, and when it could not be loaded (docs/frontend-structure.md, 7).
 */
import { Alert, Button, Center, Group, Loader, Text } from '@mantine/core';

import { ApiError } from '../api/client';

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <Center py="xl" role="status" aria-live="polite">
      <Group gap="sm">
        <Loader size="sm" />
        <Text size="sm" c="dimmed">
          {label}
        </Text>
      </Group>
    </Center>
  );
}

/** One muted sentence where a list has nothing in it: "Nobody yet." */
export function EmptyState({ children }: { children: string }) {
  return (
    <Text size="sm" c="dimmed" py="md">
      {children}
    </Text>
  );
}

function messageOf(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return 'Something went wrong. Please try again.';
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <Alert color="red" variant="light" title="This could not be loaded" role="alert" maw={640} w="100%">
      <Text size="sm" mb={onRetry ? 'sm' : 0}>
        {messageOf(error)}
      </Text>
      {onRetry ? (
        <Button size="xs" variant="default" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </Alert>
  );
}
