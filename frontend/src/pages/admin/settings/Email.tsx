/**
 * Settings › Email: everything about sending email on one page, in tabs
 * (agreed 2026-10-16; before, it was spread over General, Notifications,
 * Mailings, Mail accounts and Test email) --
 *
 * - **Senders**: which mail account sends what -- a default, and for welcome
 *   emails, notifications, announcements and team mailings, and replies to
 *   contact messages an account of their own, each "empty: the default";
 *   a backup that sends what an account could not (not the mailings);
 *   whether each reply also goes, as a blind copy, to its account's address;
 * - **What goes out**: the welcome email, which notifications go out and how
 *   each channel stands, the mailings' pace;
 * - **Accounts** (with settings.credentials) and **Test** (with
 *   notifications.manage).
 *
 * Data: /api/v1/admin/settings/email/senders and .../sending
 * (aeronautics_members/api/email_settings.py), /api/v1/admin/settings/mail
 * and .../test-email (api/admin_mail.py).
 */
import { Checkbox, NumberInput, Select, SimpleGrid, Stack, Table, Text } from '@mantine/core';
import {
  IconAddressBook,
  IconBell,
  IconMail,
  IconMailForward,
  IconSend,
  IconServer,
} from '@tabler/icons-react';
import { useState } from 'react';

import { api, call, type Schemas } from '../../../api/client';
import { can, useMe } from '../../../api/session';
import { PageTabs } from '../../../components/PageTabs';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { formatDateTime } from '../../../lib/format';
import { MailAccountsTab } from './MailAccounts';
import { SaveBar, SettingsHeader, SettingsLoaded, useSectionSave } from './shared';
import { TestEmailTab } from './TestEmail';

type SendersOut = Schemas['SendersOut'];
type SendersIn = Schemas['SendersIn'];
type SendingOut = Schemas['SendingOut'];
type SendingIn = Schemas['SendingIn'];

const sendersQuery = {
  queryKey: ['admin', 'settings', 'email', 'senders'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/email/senders')),
};

const sendingQuery = {
  queryKey: ['admin', 'settings', 'email', 'sending'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/email/sending')),
};

const BELOW = ['label', 'input', 'description', 'error'] as ('label' | 'input' | 'description' | 'error')[];

// --- Senders -----------------------------------------------------------------------------

const KINDS: {
  key: 'welcome' | 'notifications' | 'mailings' | 'replies';
  label: string;
  description: string;
}[] = [
  { key: 'welcome', label: 'Welcome emails', description: 'To a new member, and to a team’s.' },
  {
    key: 'notifications',
    label: 'Notifications',
    description: 'To the admins (what waits, errors) and to members about their membership.',
  },
  {
    key: 'mailings',
    label: 'Announcements and team mailings',
    description: 'And the contact form’s messages to the admins.',
  },
  {
    key: 'replies',
    label: 'Replies to contact messages',
    description: 'Written in Admin › Messages. Answers to them arrive in this account’s mailbox.',
  },
];

function SendersForm({ data }: { data: SendersOut }) {
  const [value, setValue] = useState<Required<SendersIn>>({
    default: data.default,
    backup: data.backup,
    welcome: data.welcome,
    notifications: data.notifications,
    mailings: data.mailings,
    replies: data.replies,
    reply_copy: data.reply_copy,
  });
  const { save, errors, clear } = useSectionSave(sendersQuery.queryKey, (body: SendersIn) =>
    call(api.PUT('/api/v1/admin/settings/email/senders', { body })),
  );
  const change = <K extends keyof SendersIn>(key: K, next: Required<SendersIn>[K]) => {
    setValue({ ...value, [key]: next });
    clear(key);
  };
  const defaultShown = value.default || data.default_in_use;
  return (
    <Stack gap="lg">
      <Panel icon={IconAddressBook} title="Which account sends what">
        <Stack gap="md">
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Select
              label="Default"
              description="Everything below left empty, and the account emails: password reset, confirming an address."
              inputWrapperOrder={BELOW}
              placeholder={
                data.default_in_use ? `The first account (${data.default_in_use})` : 'No account yet'
              }
              data={data.accounts}
              clearable
              value={value.default || null}
              error={errors.default}
              onChange={(account) => {
                change('default', account ?? '');
              }}
            />
            <Select
              label="Backup"
              description="Sends what another account could not. Announcements and team mailings wait instead."
              inputWrapperOrder={BELOW}
              placeholder="None"
              data={data.accounts}
              clearable
              value={value.backup || null}
              error={errors.backup}
              onChange={(account) => {
                change('backup', account ?? '');
              }}
            />
          </SimpleGrid>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            {KINDS.map((kind) => (
              <Select
                key={kind.key}
                label={kind.label}
                description={kind.description}
                inputWrapperOrder={BELOW}
                placeholder={defaultShown ? `The default (${defaultShown})` : 'The default'}
                data={data.accounts}
                clearable
                value={value[kind.key] || null}
                error={errors[kind.key]}
                onChange={(account) => {
                  change(kind.key, account ?? '');
                }}
              />
            ))}
          </SimpleGrid>
          <Checkbox
            label="A copy of each reply to its own address"
            description={`So the whole conversation is in that mailbox${data.reply_address ? ` (${data.reply_address})` : ''}.`}
            checked={value.reply_copy}
            onChange={(event) => {
              change('reply_copy', event.currentTarget.checked);
            }}
          />
        </Stack>
      </Panel>
      <SaveBar
        busy={save.isPending}
        onSave={() => {
          save.mutate(value);
        }}
      />
    </Stack>
  );
}

// --- What goes out ------------------------------------------------------------------------------

function Channels({ health }: { health: SendingOut['health'] }) {
  return (
    <Panel icon={IconBell} title="How each channel stands" flush>
      <Table.ScrollContainer minWidth={720} type="native">
        <Table verticalSpacing="sm" aria-label="Notification channels">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Channel</Table.Th>
              <Table.Th scope="col">On</Table.Th>
              <Table.Th scope="col">Waiting</Table.Th>
              <Table.Th scope="col">Held back</Table.Th>
              <Table.Th scope="col">Next sent</Table.Th>
              <Table.Th scope="col">After failures</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {health.map((channel) => (
              <Table.Tr key={channel.channel}>
                <Table.Td>{channel.label}</Table.Td>
                <Table.Td>
                  {channel.enabled ? <Pill tone="active">On</Pill> : <Pill tone="neutral">Off</Pill>}
                </Table.Td>
                <Table.Td className="ja-figures">{channel.pending}</Table.Td>
                <Table.Td>{channel.held_back.length ? channel.held_back.join(', ') : 'Nothing'}</Table.Td>
                <Table.Td>{channel.next_send_at ? formatDateTime(channel.next_send_at) : 'Now'}</Table.Td>
                <Table.Td>
                  {channel.backoff_until ? `Nothing before ${formatDateTime(channel.backoff_until)}` : '–'}
                  {channel.last_failure ? (
                    <Text size="xs" c="dimmed">
                      {channel.last_failure}
                    </Text>
                  ) : null}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Panel>
  );
}

function SendingForm({ data }: { data: SendingOut }) {
  const [value, setValue] = useState<SendingIn>({
    automatic_emails: data.automatic_emails,
    legal_pdfs_in_welcome_emails: data.legal_pdfs_in_welcome_emails,
    automatic_email_template: data.automatic_email_template,
    admin_general: data.admin_general,
    admin_error: data.admin_error,
    user_status: data.user_status,
    per_hour: data.per_hour,
    per_day: data.per_day,
  });
  const { save, errors, clear } = useSectionSave(sendingQuery.queryKey, (body: SendingIn) =>
    call(api.PUT('/api/v1/admin/settings/email/sending', { body })),
  );
  const change = <K extends keyof SendingIn>(key: K, next: SendingIn[K]) => {
    setValue({ ...value, [key]: next });
    clear(key);
  };
  const check = (
    key:
      'automatic_emails' | 'legal_pdfs_in_welcome_emails' | 'admin_general' | 'admin_error' | 'user_status',
  ) =>
    ({
      checked: value[key],
      onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
        change(key, event.currentTarget.checked);
      },
    }) as const;
  return (
    <Stack gap="lg">
      <Panel icon={IconMail} title="Welcome email">
        <Stack gap="md">
          <Checkbox
            label="Send it by itself"
            description="To every new member once their membership starts. Off, it is sent only by hand."
            {...check('automatic_emails')}
          />
          <Select
            label="Template"
            placeholder="No template: no welcome email"
            data={data.templates}
            clearable
            value={value.automatic_email_template ?? null}
            error={errors.automatic_email_template}
            onChange={(template) => {
              change('automatic_email_template', template);
            }}
            maw={420}
          />
          <Checkbox
            label="Attach the legal texts as PDFs"
            description="The texts the member accepted at signup, in the version they accepted. A team's welcome email gets that team's rules."
            {...check('legal_pdfs_in_welcome_emails')}
          />
        </Stack>
      </Panel>
      <Panel icon={IconBell} title="Notifications">
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            Admins get these once their email address is verified. What waits for review and application
            errors are sent as digests, each with its own cooldown; emails to members go out at once.
          </Text>
          <Checkbox label="What waits for review, to the admins" {...check('admin_general')} />
          <Checkbox label="Application errors, to the admins" {...check('admin_error')} />
          <Checkbox label="Emails to members about their membership" {...check('user_status')} />
        </Stack>
      </Panel>
      <Panel icon={IconSend} title="Announcements and team mailings">
        <Stack gap="md">
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput
              label="At most an hour"
              inputWrapperOrder={BELOW}
              min={1}
              allowDecimal={false}
              allowNegative={false}
              value={value.per_hour}
              error={errors.per_hour}
              onChange={(next) => {
                change('per_hour', Number(next));
              }}
            />
            <NumberInput
              label="At most a day"
              description="Brevo's free plan sends 300 a day."
              inputWrapperOrder={BELOW}
              min={1}
              allowDecimal={false}
              allowNegative={false}
              value={value.per_day}
              error={errors.per_day}
              onChange={(next) => {
                change('per_day', Number(next));
              }}
            />
          </SimpleGrid>
          <Text size="sm" c="dimmed">
            They go out a few at a time, every two minutes, within these limits.
          </Text>
        </Stack>
      </Panel>
      <SaveBar
        busy={save.isPending}
        onSave={() => {
          save.mutate(value);
        }}
      />
      <Channels health={data.health} />
    </Stack>
  );
}

export function Email() {
  const me = useMe().data;
  return (
    <>
      <SettingsHeader
        title="Email"
        description="Which account sends what, what goes out, the mail accounts, and a test email."
      />
      <PageTabs
        tabs={[
          {
            value: 'senders',
            label: 'Senders',
            icon: IconAddressBook,
            content: (
              <SettingsLoaded query={sendersQuery}>{(data) => <SendersForm data={data} />}</SettingsLoaded>
            ),
          },
          {
            value: 'sending',
            label: 'What goes out',
            icon: IconSend,
            content: (
              <SettingsLoaded query={sendingQuery}>{(data) => <SendingForm data={data} />}</SettingsLoaded>
            ),
          },
          {
            value: 'accounts',
            label: 'Accounts',
            icon: IconServer,
            content: can(me, 'settings.credentials') ? <MailAccountsTab /> : null,
          },
          {
            value: 'test',
            label: 'Test',
            icon: IconMailForward,
            content: can(me, 'notifications.manage') ? <TestEmailTab /> : null,
          },
        ]}
      />
    </>
  );
}
