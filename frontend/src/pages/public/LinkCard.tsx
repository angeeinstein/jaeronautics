/**
 * Where a reader's QR code leads (docs/cards-plan.md, "Linking a card at a
 * reader with a QR code"): the card just tapped there, linked to the account
 * signed in here. Opening the page opens the link at once -- the reader then
 * moves on -- and this phone keeps it: the secret it is given (``claim``) is
 * remembered in the browser, so signing in first comes back to it. Tapping
 * the card again on the reader starts over.
 *
 * Data: POST /api/v1/card-links/<token>/open and POST /api/v1/card-links/<token>
 * (aeronautics_members/api/cards.py).
 */
import { Alert, Button, Group, Stack, Text, TextInput, Title } from '@mantine/core';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router';

import { api, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { ErrorState, LoadingState } from '../../components/States';
import { formatDateTime } from '../../lib/format';
import { notifyFailed } from '../../lib/notify';

const stored = (token: string) => `card-link:${token}`;

function rememberedClaim(token: string): string | null {
  try {
    return window.localStorage.getItem(stored(token));
  } catch {
    return null;
  }
}

function remember(token: string, claim: string | null) {
  try {
    if (claim) window.localStorage.setItem(stored(token), claim);
    else window.localStorage.removeItem(stored(token));
  } catch {
    // Private browsing: it then works as long as the page stays open.
  }
}

export function LinkCard() {
  useDocumentTitle('Link a card');
  const { token = '' } = useParams();
  const [name, setName] = useState('');
  const opened = useQuery({
    queryKey: ['card-link', token],
    queryFn: async () => {
      const link = await call(
        api.POST('/api/v1/card-links/{token}/open', {
          params: { path: { token } },
          body: { claim: rememberedClaim(token) },
        }),
      );
      remember(token, link.claim);
      return link;
    },
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const link = useMutation({
    mutationFn: (claim: string) =>
      call(
        api.POST('/api/v1/card-links/{token}', {
          params: { path: { token } },
          body: { claim, name: name.trim() || null },
        }),
      ),
    onSuccess: () => {
      remember(token, null);
    },
    onError: notifyFailed,
  });

  return (
    <Stack gap="lg" maw={460} mx="auto" mt={{ base: 0, sm: 'xl' }}>
      <Title order={1}>Link a card</Title>
      {opened.isPending ? (
        <LoadingState />
      ) : opened.isError ? (
        <ErrorState error={opened.error} />
      ) : link.isSuccess ? (
        <Panel title={`Linked${link.data.first_name ? `, hi ${link.data.first_name}` : ''}`}>
          <Stack gap="md">
            <Alert color="green" variant="light" role="status">
              {link.data.card.name} ({link.data.card.tail}) is linked to your account. Tap it on the reader
              again.
            </Alert>
            <div>
              <Button component={AppLink} to="/account/cards" variant="default">
                My cards
              </Button>
            </div>
          </Stack>
        </Panel>
      ) : (
        <Panel title={`Card ${opened.data.card}`}>
          <Stack gap="md">
            <Text size="sm">
              Tapped on {opened.data.reader}
              {opened.data.place ? ` (${opened.data.place})` : ''} at {formatDateTime(opened.data.tapped_at)}.
              This link works until {formatDateTime(opened.data.expires_at)}.
            </Text>
            {opened.data.signed_in ? (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  link.mutate(opened.data.claim);
                }}
              >
                <Stack gap="md">
                  <TextInput
                    label="Name (optional)"
                    description="To tell your cards apart."
                    placeholder="Student card"
                    maxLength={40}
                    value={name}
                    onChange={(event) => {
                      setName(event.currentTarget.value);
                    }}
                  />
                  <Group justify="flex-end">
                    <Button type="submit" loading={link.isPending}>
                      Link to my account
                    </Button>
                  </Group>
                </Stack>
              </form>
            ) : (
              <Stack gap="sm">
                <Text size="sm">Sign in to link it to your account.</Text>
                <div>
                  <Button component={AppLink} to={`/login?next=${encodeURIComponent(`/link/${token}`)}`}>
                    Sign in
                  </Button>
                </div>
                <Text size="sm" c="dimmed">
                  No account yet? <AppLink to="/join">Join</AppLink> first, then tap your card again.
                </Text>
              </Stack>
            )}
          </Stack>
        </Panel>
      )}
    </Stack>
  );
}
