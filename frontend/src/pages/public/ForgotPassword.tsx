/**
 * Asking for a new password: a link by email. The answer is the same whether
 * or not the account exists, so the page says the same too.
 * Data: POST /api/v1/password-reset.
 */
import { Alert, Anchor, Button, Stack, Text, TextInput, Title } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { notifyFailed } from '../../lib/notify';

export function ForgotPassword() {
  useDocumentTitle('A new password');
  const [email, setEmail] = useState('');
  const ask = useMutation({
    mutationFn: () => call(api.POST('/api/v1/password-reset', { body: { email } })),
    onError: notifyFailed,
  });
  return (
    <Stack gap="lg" maw={460} mx="auto" mt={{ base: 0, sm: 'xl' }}>
      <Title order={1}>A new password</Title>
      <Panel title="Email me a link">
        {ask.data ? (
          <Alert color="green" variant="light" role="status">
            <Text size="sm">{ask.data.text}</Text>
          </Alert>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              ask.mutate();
            }}
          >
            <Stack gap="md">
              <Text size="sm" c="dimmed">
                We email you a link to choose a new password.
              </Text>
              <TextInput
                label="Email"
                type="email"
                autoComplete="email"
                required
                maxLength={255}
                value={email}
                onChange={(event) => {
                  setEmail(event.currentTarget.value);
                }}
              />
              <Button type="submit" loading={ask.isPending} fullWidth>
                Send the link
              </Button>
            </Stack>
          </form>
        )}
      </Panel>
      <Anchor component={AppLink} to="/login" ta="center" fw={600}>
        Back to signing in
      </Anchor>
    </Stack>
  );
}
