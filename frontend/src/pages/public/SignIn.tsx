/**
 * Signing in, with the private address or a confirmed university one. Goes
 * on to the page that asked for it (``?next=``, such as the forum's sign-in)
 * or the person's start page -- a whole new page, as who is signed in changed.
 * Coming from the forum without a membership, it says the forum is for members
 * and how to become one. Data: POST /api/v1/session.
 */
import { Alert, Anchor, Button, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useSearchParams } from 'react-router';

import { api, ApiError, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { messageOf } from '../../lib/notify';

export function SignIn() {
  useDocumentTitle('Sign in');
  const [params] = useSearchParams();
  const next = params.get('next');
  const forumHint =
    Boolean(next?.startsWith('/forum')) && params.get('forum_login_source') !== 'welcome_email';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const signIn = useMutation({
    mutationFn: () => call(api.POST('/api/v1/session', { body: { email, password, next } })),
    onSuccess: ({ go_to }) => {
      window.location.assign(go_to);
    },
  });
  const refusal = signIn.error instanceof ApiError ? signIn.error : null;

  return (
    <Stack gap="lg" maw={460} mx="auto" mt={{ base: 0, sm: 'xl' }}>
      <Title order={1}>Sign in</Title>
      {forumHint ? (
        <Alert color="amber" variant="light" title="The forum is for members">
          <Stack gap="xs">
            <Text size="sm">Already a member? Sign in below.</Text>
            <Text size="sm" fw={600}>
              Not a member yet? Join first: your website account and your forum access are made from that.
            </Text>
            <div>
              <Button component={AppLink} to="/join" size="xs">
                Become a member
              </Button>
            </div>
          </Stack>
        </Alert>
      ) : null}
      <Panel title="Your account">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            signIn.mutate();
          }}
        >
          <Stack gap="md">
            {signIn.error ? (
              <Alert
                color={refusal?.code === 'account_disabled' ? 'amber' : 'red'}
                variant="light"
                role="alert"
              >
                <Text size="sm">{messageOf(signIn.error)}</Text>
              </Alert>
            ) : null}
            <TextInput
              label="Email"
              description="Your private address, or your confirmed university one."
              type="email"
              autoComplete="email"
              required
              maxLength={255}
              value={email}
              onChange={(event) => {
                setEmail(event.currentTarget.value);
              }}
            />
            <PasswordInput
              label="Password"
              autoComplete="current-password"
              required
              maxLength={128}
              value={password}
              onChange={(event) => {
                setPassword(event.currentTarget.value);
              }}
            />
            <Button type="submit" loading={signIn.isPending || signIn.isSuccess} fullWidth>
              Sign in
            </Button>
          </Stack>
        </form>
      </Panel>
      <Stack gap={4} align="center">
        <Anchor component={AppLink} to="/forgot-password" fw={600}>
          Forgot your password?
        </Anchor>
        <Anchor component={AppLink} to="/join" c="dimmed" size="sm">
          No account yet? It is made when you join.
        </Anchor>
      </Stack>
    </Stack>
  );
}
