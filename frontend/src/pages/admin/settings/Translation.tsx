/**
 * Settings -> Translation (docs/legal-translation-plan.md): the services that
 * make the legal texts' English -- DeepL and Azure AI Translator, either or
 * both, one tried first and the other the fallback, as the email accounts
 * are -- and the glossary that keeps terms right. Of the keys only whether
 * one is set is shown. Data: GET|PUT /api/v1/admin/settings/translation,
 * POST .../<service>/try.
 */
import {
  ActionIcon,
  Alert,
  Button,
  Group,
  PasswordInput,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { IconBook, IconLanguage, IconTrash } from '@tabler/icons-react';

import { api, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { Panel } from '../../../components/Panel';
import { notifyFailed } from '../../../lib/notify';
import { SaveBar, secretHint, SettingsPage, useNotMyLogin, useSectionSave } from './shared';

type TranslationOut = Schemas['TranslationOut'];
type TranslationIn = Schemas['TranslationIn'];
type Service = 'deepl' | 'azure';

const translationQuery = {
  queryKey: ['admin', 'settings', 'translation'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/settings/translation')),
};

const LABELS: Record<Service, string> = { deepl: 'DeepL', azure: 'Azure AI Translator' };

function TryIt({ service }: { service: Service }) {
  const [said, setSaid] = useState<{ ok: boolean; message: string } | null>(null);
  const tried = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/admin/settings/translation/{name}/try', { params: { path: { name: service } } }),
      ),
    onSuccess: setSaid,
    onError: notifyFailed,
  });
  return (
    <Stack gap={6}>
      <Group>
        <Button
          variant="default"
          size="xs"
          loading={tried.isPending}
          onClick={() => {
            tried.mutate();
          }}
        >
          Try it
        </Button>
        <Text size="xs" c="dimmed">
          Translates one sentence with the key as saved.
        </Text>
      </Group>
      {said ? (
        <Alert color={said.ok ? 'green' : 'red'} variant="light" p="xs">
          <Text size="sm">{said.message}</Text>
        </Alert>
      ) : null}
    </Stack>
  );
}

function Form({ data }: { data: TranslationOut }) {
  const keySet = (service: Service) =>
    data.services.find((entry) => entry.name === service)?.key_set ?? false;
  const [value, setValue] = useState<TranslationIn>({
    order: data.services.map((entry) => entry.name),
    deepl_key: null,
    azure_key: null,
    azure_region: data.azure_region,
    glossary: data.glossary,
    forget: [],
  });
  const { save, errors, clear } = useSectionSave(translationQuery.queryKey, (body: TranslationIn) =>
    call(api.PUT('/api/v1/admin/settings/translation', { body })),
  );
  const set = <K extends keyof TranslationIn>(key: K, next: TranslationIn[K]) => {
    setValue((now) => ({ ...now, [key]: next }));
    clear(key);
  };
  const notDeepl = useNotMyLogin(true);
  const notAzure = useNotMyLogin(true);
  const first = value.order[0] ?? 'deepl';
  const glossary = value.glossary ?? [];
  const setTerm = (index: number, part: 0 | 1, text: string) => {
    set(
      'glossary',
      glossary.map((entry, at) =>
        at === index ? (part === 0 ? [text, entry[1] ?? ''] : [entry[0] ?? '', text]) : entry,
      ),
    );
  };
  const forget = (service: Service) => {
    save.mutate({ ...value, forget: [service] });
  };

  return (
    <Stack gap="lg">
      <Panel icon={IconLanguage} title="Services">
        <Stack gap="lg">
          <Text size="sm" c="dimmed">
            They make the English of the legal texts from the German. Either is enough; with both, the second
            is used when the first fails or has used up its characters. Both are free up to a limit: DeepL
            500,000 characters a month (a Free key ends in :fx), Azure 2,000,000.
          </Text>
          <Stack gap={6}>
            <Text size="sm" fw={500}>
              Tried first
            </Text>
            <SegmentedControl
              value={first}
              onChange={(chosen) => {
                const next: Service = chosen === 'azure' ? 'azure' : 'deepl';
                set('order', [next, next === 'deepl' ? 'azure' : 'deepl']);
              }}
              data={[
                { value: 'deepl', label: 'DeepL' },
                { value: 'azure', label: 'Azure AI Translator' },
              ]}
            />
          </Stack>
          <Stack gap="sm">
            <Text fw={600}>{LABELS.deepl}</Text>
            <PasswordInput
              label="Authentication key"
              description={secretHint(keySet('deepl'))}
              name="deepl-key"
              {...notDeepl}
              value={value.deepl_key ?? ''}
              error={errors.deepl_key}
              onChange={(event) => {
                set('deepl_key', event.currentTarget.value || null);
              }}
            />
            {data.deepl_limit ? (
              <Text size="sm" c="dimmed">
                {`${(data.deepl_used ?? 0).toLocaleString('en-GB')} of ${data.deepl_limit.toLocaleString('en-GB')} characters used this month.`}
              </Text>
            ) : null}
            {keySet('deepl') ? (
              <Group gap="sm">
                <TryIt service="deepl" />
                <ConfirmButton
                  size="xs"
                  confirmLabel="Yes, remove it"
                  onConfirm={() => {
                    forget('deepl');
                  }}
                >
                  Remove the key
                </ConfirmButton>
              </Group>
            ) : null}
          </Stack>
          <Stack gap="sm">
            <Text fw={600}>{LABELS.azure}</Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              <PasswordInput
                label="Key"
                description={secretHint(keySet('azure'))}
                name="azure-translator-key"
                {...notAzure}
                value={value.azure_key ?? ''}
                error={errors.azure_key}
                onChange={(event) => {
                  set('azure_key', event.currentTarget.value || null);
                }}
              />
              <TextInput
                label="Region"
                description="The resource's location, like westeurope; empty for a global one."
                value={value.azure_region ?? ''}
                error={errors.azure_region}
                onChange={(event) => {
                  set('azure_region', event.currentTarget.value);
                }}
              />
            </SimpleGrid>
            {keySet('azure') ? (
              <Group gap="sm">
                <TryIt service="azure" />
                <ConfirmButton
                  size="xs"
                  confirmLabel="Yes, remove it"
                  onConfirm={() => {
                    forget('azure');
                  }}
                >
                  Remove the key
                </ConfirmButton>
              </Group>
            ) : null}
          </Stack>
        </Stack>
      </Panel>
      <Panel icon={IconBook} title="Glossary">
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            Terms always translated the same way. DeepL also finds other forms of a word (des Vorstands);
            Azure only the word as written here. A changed glossary is used from the next translation on.
          </Text>
          {glossary.map((entry, index) => (
            <Group key={index} gap="sm" wrap="nowrap" align="flex-end">
              <TextInput
                aria-label={`German term ${String(index + 1)}`}
                placeholder="German"
                maxLength={100}
                style={{ flex: 1 }}
                value={entry[0] ?? ''}
                onChange={(event) => {
                  setTerm(index, 0, event.currentTarget.value);
                }}
              />
              <TextInput
                aria-label={`English term ${String(index + 1)}`}
                placeholder="English"
                maxLength={100}
                style={{ flex: 1 }}
                value={entry[1] ?? ''}
                onChange={(event) => {
                  setTerm(index, 1, event.currentTarget.value);
                }}
              />
              <ActionIcon
                variant="default"
                size="lg"
                aria-label={`Remove ${entry[0] ?? 'this term'}`}
                onClick={() => {
                  set(
                    'glossary',
                    glossary.filter((_entry, at) => at !== index),
                  );
                }}
              >
                <IconTrash size={16} />
              </ActionIcon>
            </Group>
          ))}
          {errors.glossary ? (
            <Text size="sm" c="red">
              {errors.glossary}
            </Text>
          ) : null}
          <Group>
            <Button
              variant="default"
              size="xs"
              disabled={glossary.length >= 200}
              onClick={() => {
                set('glossary', [...glossary, ['', '']]);
              }}
            >
              Add a term
            </Button>
          </Group>
        </Stack>
      </Panel>
      <SaveBar
        busy={save.isPending}
        onSave={() => {
          save.mutate({ ...value, forget: [] });
        }}
      />
    </Stack>
  );
}

export function Translation() {
  return (
    <SettingsPage
      title="Translation"
      description="The services that make the legal texts' English, and the glossary."
      query={translationQuery}
    >
      {(data) => <Form data={data} />}
    </SettingsPage>
  );
}
