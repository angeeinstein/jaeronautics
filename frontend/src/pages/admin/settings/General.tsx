/**
 * Settings -> General: how members can pay at signup, the welcome email, and
 * the domains a student's address must be on. Data: GET|PUT
 * /api/v1/admin/settings/general (aeronautics_members/api/admin_settings.py).
 */
import { Checkbox, Select, Stack, Text, TextInput } from '@mantine/core';
import { useState } from 'react';

import { api, call, type Schemas } from '../../../api/client';
import { Panel } from '../../../components/Panel';
import { SaveBar, SettingsPage, useSectionSave } from './shared';

type GeneralOut = Schemas['GeneralOut'];
type GeneralIn = Schemas['GeneralIn'];

const generalQuery = {
  queryKey: ['admin', 'settings', 'general'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/general')),
};

function Form({ data }: { data: GeneralOut }) {
  const [value, setValue] = useState<GeneralIn>({
    invoice_payments: data.invoice_payments,
    automatic_emails: data.automatic_emails,
    legal_pdfs_in_welcome_emails: data.legal_pdfs_in_welcome_emails,
    welcome_email_sender: data.welcome_email_sender,
    automatic_email_template: data.automatic_email_template,
    institutional_email_domains: data.institutional_email_domains,
    staff_email_domains: data.staff_email_domains,
  });
  const { save, errors, clear } = useSectionSave(generalQuery.queryKey, (body: GeneralIn) =>
    call(api.PUT('/api/v1/admin/settings/general', { body })),
  );
  const change = <K extends keyof GeneralIn>(key: K, next: GeneralIn[K]) => {
    setValue({ ...value, [key]: next });
    clear(key);
  };

  return (
    <Stack gap="lg">
      <Panel title="Signing up">
        <Checkbox
          label="Paying by invoice"
          description="Offered beside paying online when somebody joins."
          checked={value.invoice_payments}
          onChange={(event) => {
            change('invoice_payments', event.currentTarget.checked);
          }}
        />
      </Panel>
      <Panel title="Welcome email">
        <Stack gap="md">
          <Checkbox
            label="Send it by itself"
            description="To every new member once their membership starts. Off, it is sent only by hand."
            checked={value.automatic_emails}
            onChange={(event) => {
              change('automatic_emails', event.currentTarget.checked);
            }}
          />
          <Select
            label="From"
            placeholder="No sender: no welcome email"
            data={data.senders}
            clearable
            value={value.welcome_email_sender ?? null}
            error={errors.welcome_email_sender}
            onChange={(sender) => {
              change('welcome_email_sender', sender);
            }}
            maw={420}
          />
          <Select
            label="Template"
            placeholder="No template"
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
            checked={value.legal_pdfs_in_welcome_emails}
            onChange={(event) => {
              change('legal_pdfs_in_welcome_emails', event.currentTarget.checked);
            }}
          />
        </Stack>
      </Panel>
      <Panel title="University email domains">
        <Stack gap="md">
          <Stack gap="xs">
            <TextInput
              label="Students"
              description="A student joining must give an address on one of these: it shows they study here now."
              placeholder="edu.fh-joanneum.at"
              value={value.institutional_email_domains ?? ''}
              error={errors.institutional_email_domains}
              onChange={(event) => {
                change('institutional_email_domains', event.currentTarget.value || null);
              }}
            />
            <Text size="sm" c="dimmed">
              In use now: {data.domains_in_use.join(', ')}
            </Text>
          </Stack>
          <Stack gap="xs">
            <TextInput
              label="Staff"
              description="Staff and lecturers give an address on one of these, and may sign in with it. A student's address never counts as one."
              placeholder="fh-joanneum.at"
              value={value.staff_email_domains ?? ''}
              error={errors.staff_email_domains}
              onChange={(event) => {
                change('staff_email_domains', event.currentTarget.value || null);
              }}
            />
            <Text size="sm" c="dimmed">
              In use now: {data.staff_domains_in_use.join(', ')}
            </Text>
          </Stack>
          <Text size="xs" c="dimmed">
            Separate them with commas; subdomains count. Empty keeps the built-in list rather than blocking
            everyone.
          </Text>
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

export function General() {
  return (
    <SettingsPage
      title="General"
      description="Signing up, the welcome email, and the students' email domains."
      query={generalQuery}
    >
      {(data) => <Form data={data} />}
    </SettingsPage>
  );
}
