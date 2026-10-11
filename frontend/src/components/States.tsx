/**
 * What a page or a card shows while its data is not there yet, when there is
 * none, and when it could not be loaded (docs/frontend-structure.md, 7).
 */
import { Alert, Button, Center, Group, Loader, Text } from '@mantine/core';
import { useEffect, useRef } from 'react';

import { ApiError, UPDATING } from '../api/client';

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

/** How often a page waiting out an update asks again. */
export const UPDATING_RETRY_MS = 5000;

/** The portal is down for an update for a minute at most: say so, and load again by itself. */
function Updating({ onRetry }: { onRetry?: () => void }) {
  // The latest one, without starting the wait over whenever the page renders.
  const retry = useRef(onRetry);
  useEffect(() => {
    retry.current = onRetry;
  });
  useEffect(() => {
    const timer = window.setInterval(() => retry.current?.(), UPDATING_RETRY_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, []);
  return (
    <Alert color="brand" variant="light" title="Update in progress" role="status" maw={640} w="100%">
      <Group gap="sm" wrap="nowrap">
        <Loader size="xs" />
        <Text size="sm">The portal is being updated. This loads again by itself in a moment.</Text>
      </Group>
    </Alert>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (error instanceof ApiError && error.code === UPDATING) return <Updating onRetry={onRetry} />;
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
