/**
 * Settings -> Forum: the forum's address and credentials, its groups -- who
 * is a member, onboarding, inactive, staff, and a group per kind of member --
 * who may read the lecture material, the profile pictures, and the portal's
 * addresses to set up on the forum, with a test of the connection. Of the
 * secrets only whether one is set is ever shown. Data: GET|PUT
 * /api/v1/admin/settings/forum, POST .../forum/test.
 */
import {
  Alert,
  Button,
  Checkbox,
  Code,
  Group,
  NumberInput,
  PasswordInput,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call, type Schemas } from '../../../api/client';
import { type Detail, Details } from '../../../components/Details';
import { Panel } from '../../../components/Panel';
import { notifyFailed } from '../../../lib/notify';
import { SaveBar, secretHint, SettingsPage, useSectionSave } from './shared';

type ForumOut = Schemas['ForumSettingsOut'];
type ForumIn = Schemas['ForumIn'];

const forumQuery = {
  queryKey: ['admin', 'settings', 'forum'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/forum')),
};

function initial(data: ForumOut): ForumIn {
  return {
    enabled: data.enabled,
    base_url: data.base_url,
    api_username: data.api_username,
    api_key: null,
    connect_secret: null,
    onboarding_group: data.onboarding_group,
    member_group: data.member_group,
    inactive_group: data.inactive_group,
    staff_group: data.staff_group,
    manage_staff_flags: data.manage_staff_flags,
    category_groups: Object.fromEntries(data.category_groups.map((item) => [item.kind, item.group])),
    lecture_groups: data.lecture_groups,
    archive_groups: data.archive_groups,
    onboarding_path: data.onboarding_path,
    avatar_max_bytes: data.avatar_max_bytes,
    avatar_allowed_types: data.avatar_allowed_types,
  };
}

function Connection({ data }: { data: ForumOut }) {
  const [result, setResult] = useState<Schemas['ConnectionOut'] | null>(null);
  const test = useMutation({
    mutationFn: () => call(api.POST('/api/v1/admin/settings/forum/test')),
    onSuccess: setResult,
    onError: notifyFailed,
  });
  return (
    <Panel title="Connection">
      <Stack gap="md">
        <Details
          items={[
            ...(data.endpoints.public_base_url
              ? [['The portal', <Code key="base">{data.endpoints.public_base_url}</Code>] satisfies Detail]
              : []),
            ['Members enter the forum at', <Code key="entry">{data.endpoints.entry}</Code>],
            ['DiscourseConnect callback', <Code key="connect">{data.endpoints.connect}</Code>],
            ['Logout redirect', <Code key="logout">{data.endpoints.logout}</Code>],
          ]}
        />
        <Text size="sm" c="dimmed">
          Set Discourse&apos;s logout redirect to the address above, so signing out of the forum signs out of
          the portal too.
        </Text>
        {data.missing.length ? (
          <Alert color="amber" variant="light">
            Switched on, but still missing: {data.missing.join(', ')}.
          </Alert>
        ) : null}
        <Group gap="md" align="center">
          <Button
            variant="default"
            loading={test.isPending}
            onClick={() => {
              test.mutate();
            }}
          >
            Test the connection
          </Button>
          <Text size="sm" c="dimmed">
            Checks the forum answers with these settings, as saved. Grants nobody anything.
          </Text>
        </Group>
        {result ? (
          <Alert color={result.ok ? 'green' : 'red'} variant="light" role="status">
            {result.message}
          </Alert>
        ) : null}
      </Stack>
    </Panel>
  );
}

function Form({ data }: { data: ForumOut }) {
  const [value, setValue] = useState<ForumIn>(initial(data));
  const { save, errors, clear } = useSectionSave(forumQuery.queryKey, (body: ForumIn) =>
    call(api.PUT('/api/v1/admin/settings/forum', { body })),
  );
  const text = (
    key: keyof ForumIn,
    label: string,
    extra: { description?: string; placeholder?: string } = {},
  ) => (
    <TextInput
      label={label}
      {...extra}
      value={(value[key] as string | null | undefined) ?? ''}
      error={errors[key]}
      onChange={(event) => {
        setValue({ ...value, [key]: event.currentTarget.value });
        clear(key);
      }}
    />
  );

  return (
    <Stack gap="lg">
      <Panel title="The forum">
        <Stack gap="md">
          <Checkbox
            label="Connected"
            description="Members reach the forum through the portal, and their groups follow their membership."
            checked={value.enabled}
            onChange={(event) => {
              setValue({ ...value, enabled: event.currentTarget.checked });
            }}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            {text('base_url', 'Address', { placeholder: 'https://forum.example.com' })}
            {text('api_username', 'API username', { placeholder: 'system' })}
            <PasswordInput
              label="API key"
              description={secretHint(data.api_key_set)}
              autoComplete="off"
              value={value.api_key ?? ''}
              onChange={(event) => {
                setValue({ ...value, api_key: event.currentTarget.value || null });
              }}
            />
            <PasswordInput
              label="DiscourseConnect secret"
              description={secretHint(data.connect_secret_set)}
              autoComplete="off"
              value={value.connect_secret ?? ''}
              onChange={(event) => {
                setValue({ ...value, connect_secret: event.currentTarget.value || null });
              }}
            />
            {text('onboarding_path', 'Where members land', {
              description: 'On the forum, after signing in with an approved picture.',
              placeholder: '/',
            })}
          </SimpleGrid>
        </Stack>
      </Panel>
      <Panel title="Groups">
        <Stack gap="md">
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="md">
            {text('member_group', 'Members')}
            {text('onboarding_group', 'Onboarding', { description: 'Paid, picture not approved yet.' })}
            {text('inactive_group', 'Inactive (optional)')}
            {text('staff_group', 'Staff (optional)')}
          </SimpleGrid>
          <Text size="sm" c="dimmed">
            The staff group is for whoever may administer the forum here; it follows this portal on every
            sync, so nobody keeps it up on the forum.
          </Text>
          <Checkbox
            label="This portal decides who is admin or moderator on the forum"
            description="Super Admins become forum admins; Admins and Forum moderators become moderators, on every sync. Anybody else synced from here loses the flag, so give the roles first, then switch this on."
            checked={value.manage_staff_flags}
            onChange={(event) => {
              setValue({ ...value, manage_staff_flags: event.currentTarget.checked });
            }}
          />
          <Stack gap={4}>
            <Text fw={600} size="sm">
              A group per kind of member (optional)
            </Text>
            <Text size="sm" c="dimmed">
              You choose the names, and the groups are made on the forum for you; a kind left empty is in no
              such group. This portal decides who is in which group, the forum what each group may see.
            </Text>
          </Stack>
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
            {data.category_groups.map((item) => (
              <TextInput
                key={item.kind}
                label={item.label}
                placeholder="No group"
                value={value.category_groups?.[item.kind] ?? ''}
                onChange={(event) => {
                  setValue({
                    ...value,
                    category_groups: { ...value.category_groups, [item.kind]: event.currentTarget.value },
                  });
                }}
              />
            ))}
          </SimpleGrid>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            {text('lecture_groups', 'May read the lecture material', {
              description:
                'Not the member group: lecturers and company representatives are paying members too, and this is a decade of exams about their lectures.',
              placeholder: 'students, alumni',
            })}
            {text('archive_groups', 'May read the archive', {
              description: 'Read and search only. Empty: the same as above.',
            })}
          </SimpleGrid>
        </Stack>
      </Panel>
      <Panel title="Profile pictures">
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <NumberInput
            label="Largest file"
            description="In bytes."
            min={1}
            allowDecimal={false}
            thousandSeparator=","
            value={value.avatar_max_bytes ?? ''}
            error={errors.avatar_max_bytes}
            onChange={(size) => {
              setValue({ ...value, avatar_max_bytes: typeof size === 'number' ? size : null });
              clear('avatar_max_bytes');
            }}
          />
          <TextInput
            label="File types"
            placeholder="jpg, jpeg, png, webp"
            value={(value.avatar_allowed_types ?? []).join(', ')}
            onChange={(event) => {
              setValue({
                ...value,
                avatar_allowed_types: event.currentTarget.value.split(',').map((type) => type.trim()),
              });
            }}
          />
        </SimpleGrid>
      </Panel>
      <SaveBar
        busy={save.isPending}
        onSave={() => {
          save.mutate({
            ...value,
            avatar_allowed_types: (value.avatar_allowed_types ?? []).filter(Boolean),
          });
        }}
      />
      <Connection data={data} />
    </Stack>
  );
}

export function Forum() {
  return (
    <SettingsPage
      title="Forum"
      description="The forum's address, credentials and groups, and the portal's addresses to set up there."
      query={forumQuery}
    >
      {(data) => <Form data={data} />}
    </SettingsPage>
  );
}
