/**
 * My Account › Name and kind of membership: changed only on request, which
 * an admin decides. While one waits it is shown, and can be withdrawn; else
 * the form, which asks for a year group only of those who have one
 * (member_categories.py, through GET /api/v1/forms/options).
 */
import { Button, Group, Select, SimpleGrid, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../api/client';
import { ConfirmButton } from '../../components/ConfirmButton';
import { Details } from '../../components/Details';
import { Panel } from '../../components/Panel';
import { emptyToNull } from '../../lib/forms';
import { notifyFailed } from '../../lib/notify';
import { type FormOptions, useTakeSaved } from './shared';

type Identity = Schemas['IdentityOut'];
type Request = Schemas['ChangeRequestOut'];
type RequestIn = Schemas['ChangeRequestIn'];

function fullName(person: { title: string | null; first_name: string | null; last_name: string | null }) {
  return [person.title, person.first_name, person.last_name].filter(Boolean).join(' ');
}

function Waiting({ request, options }: { request: Request; options: FormOptions }) {
  const take = useTakeSaved();
  const withdraw = useMutation({
    mutationFn: () =>
      call(
        api.DELETE('/api/v1/account/change-request/{request_id}', {
          params: { path: { request_id: request.id } },
        }),
      ),
    onSuccess: take,
    onError: notifyFailed,
  });
  const salutation = options.salutations.find((choice) => choice.value === request.salutation)?.label;
  return (
    <Stack gap="md">
      <Text size="sm">Your request is waiting for an admin. Until then, nothing changes.</Text>
      <Details
        items={[
          ['Name', fullName(request)],
          ['Salutation', salutation ?? request.salutation],
          ['Membership type', request.member_category_label],
          ['Year group', request.year_group],
          ['Your note', request.note],
          ...(request.username_could_become
            ? ([['Forum username', `Could become ${request.username_could_become}`]] as [string, string][])
            : []),
        ]}
      />
      <Group>
        <ConfirmButton
          variant="default"
          confirmLabel="Yes, withdraw"
          loading={withdraw.isPending}
          onConfirm={() => {
            withdraw.mutate();
          }}
        >
          Withdraw request
        </ConfirmButton>
      </Group>
    </Stack>
  );
}

function RequestForm({ identity, options }: { identity: Identity; options: FormOptions }) {
  const [value, setValue] = useState<RequestIn>({
    salutation: identity.salutation ?? '',
    title: identity.title ?? '',
    first_name: identity.first_name ?? '',
    last_name: identity.last_name ?? '',
    member_category: identity.member_category ?? 'student',
    year_group: identity.year_group ?? '',
    note: '',
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const take = useTakeSaved();
  const kind = options.member_categories.find((category) => category.value === value.member_category);
  const send = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/account/change-request', {
          body: {
            ...value,
            title: emptyToNull(value.title),
            year_group: kind?.year_group === 'hidden' ? null : emptyToNull(value.year_group),
            note: emptyToNull(value.note),
          },
        }),
      ),
    onSuccess: (saved) => {
      setErrors({});
      take(saved);
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const change = (key: keyof RequestIn, next: string) => {
    setValue({ ...value, [key]: next });
    if (errors[key]) setErrors({ ...errors, [key]: '' });
  };
  const text = (key: keyof RequestIn) => ({
    value: value[key] ?? '',
    error: errors[key] ?? null,
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      change(key, event.currentTarget.value);
    },
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        send.mutate();
      }}
    >
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <Select
            label="Salutation"
            data={options.salutations}
            value={value.salutation || null}
            error={errors.salutation ?? null}
            onChange={(next) => {
              change('salutation', next ?? '');
            }}
          />
          <TextInput label="Title" maxLength={50} {...text('title')} />
          <TextInput label="First name" maxLength={100} {...text('first_name')} />
          <TextInput label="Last name" maxLength={100} {...text('last_name')} />
          <Select
            label="Membership type"
            data={options.member_categories.map(({ value: category, label }) => ({ value: category, label }))}
            description={kind?.description}
            value={value.member_category}
            error={errors.member_category ?? null}
            allowDeselect={false}
            onChange={(next) => {
              change('member_category', next ?? 'student');
            }}
          />
          {kind?.year_group === 'hidden' ? null : (
            <TextInput
              label="Year group"
              description="For example LAV25."
              required={kind?.year_group === 'required'}
              maxLength={50}
              {...text('year_group')}
            />
          )}
        </SimpleGrid>
        <Textarea
          label="Why should this be changed?"
          placeholder="Optional, for example to correct a typo."
          autosize
          minRows={2}
          maxLength={1000}
          {...text('note')}
        />
        <Group justify="flex-end">
          <Button type="submit" loading={send.isPending}>
            Send for review
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

export function IdentityCard({
  identity,
  request,
  options,
}: {
  identity: Identity;
  request: Request | null;
  options: FormOptions;
}) {
  return (
    <Panel title="Name and membership type">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Changes here are checked by an admin before they take effect.
        </Text>
        {request ? (
          <Waiting request={request} options={options} />
        ) : (
          <RequestForm identity={identity} options={options} />
        )}
      </Stack>
    </Panel>
  );
}
