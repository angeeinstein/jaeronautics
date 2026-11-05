/**
 * Into the editor (docs/legal-texts-in-portal-plan.md, "The editor"): a new
 * draft written from the version in force or the template, and a draft --
 * uploaded, or a file in legal/, copied into the portal first -- opened.
 * Used on Admin › Legal texts and a team's Rules page.
 */
import { Button, Group, SegmentedControl, Select, Stack, Text } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { IconPencil } from '@tabler/icons-react';
import { useState } from 'react';
import { useNavigate } from 'react-router';

import { api, call, type Schemas } from '../../api/client';
import { Panel } from '../../components/Panel';
import { notifyFailed } from '../../lib/notify';

type WritableText = Schemas['WritableText'];

/** A draft opened in the editor: one uploaded by its id, a file in legal/ by its text and day. */
export function useEditDraft(editorUrl: (recordId: number) => string) {
  const navigate = useNavigate();
  return useMutation({
    mutationFn: async (draft: {
      record_id?: number | null;
      document?: string;
      team?: string | null;
      version: string;
    }) => {
      if (draft.record_id != null) return editorUrl(draft.record_id);
      const started = await call(
        api.POST('/api/v1/legal/imports', {
          body: { document: draft.document ?? '', team: draft.team ?? null, version: draft.version },
        }),
      );
      return started.editor_url;
    },
    onSuccess: (url) => {
      void navigate(url);
    },
    onError: notifyFailed,
  });
}

export function WriteNew({ texts, intro }: { texts: WritableText[]; intro: string }) {
  const navigate = useNavigate();
  const key = (text: WritableText) => `${text.document}|${text.team ?? ''}`;
  const [chosen, setChosen] = useState<string | null>(texts.length === 1 && texts[0] ? key(texts[0]) : null);
  const text = texts.find((candidate) => key(candidate) === chosen);
  const [start, setStart] = useState<'in_force' | 'template'>('in_force');
  const from = text && !text.has_in_force ? 'template' : start;
  const write = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/legal/drafts', {
          body: { document: text?.document ?? '', team: text?.team ?? null, start: from },
        }),
      ),
    onSuccess: (started) => {
      void navigate(started.editor_url);
    },
    onError: notifyFailed,
  });
  return (
    <Panel icon={IconPencil} title="Write a new version">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          {intro}
        </Text>
        <Group gap="md" align="flex-end" wrap="wrap">
          {texts.length > 1 ? (
            <Select
              label="Text"
              placeholder="Choose a text"
              data={texts.map((candidate) => ({ value: key(candidate), label: candidate.name }))}
              value={chosen}
              onChange={setChosen}
              searchable
              miw={240}
            />
          ) : null}
          <SegmentedControl
            aria-label="Start from"
            value={from}
            onChange={(value) => {
              setStart(value === 'template' ? 'template' : 'in_force');
            }}
            data={[
              {
                value: 'in_force',
                label: 'From the version in force',
                disabled: Boolean(text && !text.has_in_force),
              },
              { value: 'template', label: 'From the template' },
            ]}
          />
          <Button
            disabled={!text}
            loading={write.isPending}
            onClick={() => {
              write.mutate();
            }}
          >
            Write
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}
