/**
 * The link in the reset email: checked first, then the new password twice.
 * It works once. Data: GET|PUT /api/v1/password-reset/<token>.
 */
import { Anchor, Button, PasswordInput, Stack, Title } from '@mantine/core';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router';

import { api, ApiError, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { useDocumentTitle } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { ErrorState, LoadingState } from '../../components/States';
import { notifyDone, notifyFailed } from '../../lib/notify';

export function ResetPassword() {
  useDocumentTitle('Choose a new password');
  const { token = '' } = useParams();
  const navigate = useNavigate();
  const link = useQuery({
    queryKey: ['password-reset', token] as const,
    queryFn: () => call(api.GET('/api/v1/password-reset/{token}', { params: { path: { token } } })),
    retry: false,
  });
  const [password, setPassword] = useState('');
  const [again, setAgain] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () =>
      call(api.PUT('/api/v1/password-reset/{token}', { params: { path: { token } }, body: { password } })),
    onSuccess: async ({ text }) => {
      notifyDone(text);
      await navigate('/login');
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });

  return (
    <Stack gap="lg" maw={460} mx="auto" mt={{ base: 0, sm: 'xl' }}>
      <Title order={1}>Choose a new password</Title>
      {link.isPending ? (
        <LoadingState />
      ) : link.isError ? (
        <Stack gap="sm">
          <ErrorState error={link.error} />
          <Anchor component={AppLink} to="/forgot-password" fw={600}>
            Ask for a new link
          </Anchor>
        </Stack>
      ) : (
        <Panel title="Your new password">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (password !== again) setErrors({ again: 'The two passwords are not the same.' });
              else save.mutate();
            }}
          >
            <Stack gap="md">
              <PasswordInput
                label="New password"
                description="At least 8 characters."
                autoComplete="new-password"
                required
                minLength={8}
                maxLength={128}
                value={password}
                error={errors.password ?? null}
                onChange={(event) => {
                  setPassword(event.currentTarget.value);
                  setErrors({});
                }}
              />
              <PasswordInput
                label="New password again"
                autoComplete="new-password"
                required
                value={again}
                error={errors.again ?? null}
                onChange={(event) => {
                  setAgain(event.currentTarget.value);
                  setErrors({});
                }}
              />
              <Button type="submit" loading={save.isPending} fullWidth>
                Save the new password
              </Button>
            </Stack>
          </form>
        </Panel>
      )}
    </Stack>
  );
}
