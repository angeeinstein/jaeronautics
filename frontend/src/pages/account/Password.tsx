/**
 * Changing the password: the current one first, then the new one twice.
 * Data: PUT /api/v1/account/password; back to My Account when done.
 */
import { Button, Group, PasswordInput, Stack } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router';

import { api, ApiError, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { notifyFailed, notifyMessage } from '../../lib/notify';

export function Password() {
  const navigate = useNavigate();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const change = useMutation({
    mutationFn: () =>
      call(api.PUT('/api/v1/account/password', { body: { current_password: current, new_password: next } })),
    onSuccess: async (message) => {
      notifyMessage(message);
      await navigate('/account');
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const submit = () => {
    if (next !== again) {
      setErrors({ again: 'The two new passwords are not the same.' });
      return;
    }
    change.mutate();
  };
  return (
    <>
      <PageHeader
        title="Change password"
        crumbs={[{ label: 'My Account', to: '/account' }, { label: 'Password' }]}
      />
      <Panel title="Your password">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <Stack gap="md" maw={420}>
            <PasswordInput
              label="Current password"
              autoComplete="current-password"
              required
              value={current}
              error={errors.current_password ?? null}
              onChange={(event) => {
                setCurrent(event.currentTarget.value);
                setErrors({});
              }}
            />
            <PasswordInput
              label="New password"
              description="At least 8 characters."
              autoComplete="new-password"
              required
              minLength={8}
              maxLength={128}
              value={next}
              error={errors.new_password ?? null}
              onChange={(event) => {
                setNext(event.currentTarget.value);
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
            <Group gap="sm">
              <Button type="submit" loading={change.isPending}>
                Change password
              </Button>
              <Button component={AppLink} to="/account" variant="default">
                Back
              </Button>
            </Group>
          </Stack>
        </form>
      </Panel>
    </>
  );
}
