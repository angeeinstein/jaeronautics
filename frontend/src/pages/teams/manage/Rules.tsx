/**
 * A team's rules, for its leads (docs/legal-texts-in-portal-plan.md, "Team
 * rules"): the rules in force, a new version uploaded as a draft -- the
 * association's admins are told and publish it, and the leads are told when
 * it is -- and the versions waiting, each with its PDF to pass round in the
 * team. Data: GET /api/v1/teams/<slug>/manage/rules, POST .../drafts,
 * DELETE .../drafts/<id> (aeronautics_members/api/team_manage.py).
 */
import { Anchor, Button, Group, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { IconClock, IconScale, IconUpload } from '@tabler/icons-react';

import { api, call, type Schemas } from '../../../api/client';
import { ConfirmButton } from '../../../components/ConfirmButton';
import {
  formOf,
  LegalFiles,
  NO_FILES,
  Problems,
  problemsOf,
  type ChosenFiles,
} from '../../../components/legal/LegalFiles';
import { Panel } from '../../../components/Panel';
import { PdfLink } from '../../../components/PdfLink';
import { Pill } from '../../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { formatDate, formatDateTime } from '../../../lib/format';
import { notifyDone, notifyFailed } from '../../../lib/notify';
import classes from '../../admin/LegalTexts.module.css';
import { useEditDraft, WriteNew } from '../../legal/Starting';
import { ManageHeader, useSlug } from './shared';

type RulesOut = Schemas['TeamRulesOut'];

const rulesKey = (slug: string) => ['teams', slug, 'rules'] as const;

function Upload({ slug, data }: { slug: string; data: RulesOut }) {
  const client = useQueryClient();
  const [files, setFiles] = useState<ChosenFiles>(NO_FILES);
  const [problems, setProblems] = useState<string[]>([]);
  const upload = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/teams/{slug}/manage/rules/drafts', {
          params: { path: { slug } },
          body: formOf(files),
        }),
      ),
    onSuccess: (out) => {
      setProblems([]);
      setFiles(NO_FILES);
      client.setQueryData(rulesKey(slug), out);
      notifyDone('Uploaded. The association is told.');
    },
    onError: (error) => {
      const listed = problemsOf(error);
      if (listed) setProblems(listed);
      else notifyFailed(error);
    },
  });
  return (
    <Panel icon={IconUpload} title="Upload a file">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          The German Markdown file of your new rules (the English is made by machine once they are published).
          It is kept as a draft, with its PDF to pass round in the team; the association publishes it, and you
          are told when it does.
        </Text>
        <Problems problems={problems} title="Not taken, because of:" />
        <LegalFiles
          value={files}
          onChange={(chosen) => {
            setFiles(chosen);
            setProblems([]);
          }}
        />
        <Group gap="md">
          <Button
            loading={upload.isPending}
            disabled={!files.german}
            onClick={() => {
              upload.mutate();
            }}
          >
            Upload as draft
          </Button>
          <Anchor href={data.template_url} download>
            Download a template
          </Anchor>
          <Text size="sm" c="dimmed" className={classes.hint}>
            Set up for this team&apos;s rules, with everything a text can do.
          </Text>
        </Group>
      </Stack>
    </Panel>
  );
}

function Waiting({ slug, data }: { slug: string; data: RulesOut }) {
  const client = useQueryClient();
  const edit = useEditDraft((recordId) => `/teams/${slug}/manage/rules/drafts/${String(recordId)}`);
  const remove = useMutation({
    mutationFn: (recordId: number) =>
      call(
        api.DELETE('/api/v1/teams/{slug}/manage/rules/drafts/{record_id}', {
          params: { path: { slug, record_id: recordId } },
        }),
      ),
    onSuccess: (out) => {
      client.setQueryData(rulesKey(slug), out);
      notifyDone('Draft deleted.');
    },
    onError: notifyFailed,
  });
  return (
    <Panel icon={IconClock} title="Not in force yet" flush>
      {data.waiting.length ? (
        <ul className={classes.list}>
          {data.waiting.map((text) => {
            const draftId = text.deletable ? (text.record_id ?? null) : null;
            return (
              <li key={text.pdf_url} className={classes.item}>
                <Stack gap={4}>
                  <Text>{text.title}</Text>
                  <Group gap="xs">
                    <Text size="sm" c="dimmed">
                      {text.uploaded_at
                        ? `Uploaded ${formatDateTime(text.uploaded_at)}${text.uploaded_by ? ` by ${text.uploaded_by}` : ''}`
                        : `Version of ${formatDate(text.version)}`}
                      {text.has_english ? ' · with English' : ''}
                    </Text>
                    {text.status === 'draft' ? (
                      <Pill tone="neutral">Draft</Pill>
                    ) : (
                      <Pill tone="info">{`From ${formatDate(text.effective_from)}`}</Pill>
                    )}
                  </Group>
                </Stack>
                <Group gap="sm">
                  {text.status === 'draft' ? (
                    <Button
                      size="xs"
                      variant="default"
                      loading={edit.isPending && edit.variables.version === text.version}
                      onClick={() => {
                        edit.mutate({ ...text, document: 'team-rules', team: slug });
                      }}
                    >
                      Edit
                    </Button>
                  ) : null}
                  {draftId !== null ? (
                    <ConfirmButton
                      size="xs"
                      confirmLabel="Yes, delete"
                      loading={remove.isPending && remove.variables === draftId}
                      onConfirm={() => {
                        remove.mutate(draftId);
                      }}
                    >
                      Delete
                    </ConfirmButton>
                  ) : null}
                  {text.markdown_url ? (
                    <Anchor href={text.markdown_url} download size="sm">
                      Markdown
                    </Anchor>
                  ) : null}
                  <PdfLink href={text.pdf_url}>PDF</PdfLink>
                </Group>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className={classes.empty}>
          <EmptyState>No drafts, and no version waiting for its day.</EmptyState>
        </div>
      )}
    </Panel>
  );
}

export function Rules() {
  const slug = useSlug();
  const rules = useQuery({
    queryKey: rulesKey(slug),
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/rules', { params: { path: { slug } } })),
  });
  return (
    <>
      <ManageHeader
        title="Rules"
        description="The team's rules: new versions, for the association to publish."
      />
      {rules.isPending ? (
        <LoadingState />
      ) : rules.isError ? (
        <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />
      ) : (
        <Stack gap="lg">
          <Panel icon={IconScale} title="In force">
            {rules.data.in_force ? (
              <Group justify="space-between" gap="sm">
                <Stack gap={2}>
                  <Anchor href={rules.data.in_force.page_url}>{rules.data.in_force.title}</Anchor>
                  <Text size="sm" c="dimmed" lang="de">
                    {rules.data.in_force.validity}
                  </Text>
                </Stack>
                <PdfLink href={rules.data.in_force.pdf_url}>PDF</PdfLink>
              </Group>
            ) : (
              <Text c="dimmed">None yet.</Text>
            )}
          </Panel>
          <WriteNew
            texts={[
              {
                document: 'team-rules',
                team: slug,
                name: 'Teamordnung',
                has_in_force: Boolean(rules.data.in_force),
              },
            ]}
            intro="In the editor, with the page beside it as it will be. Saved as a draft as you write; the association publishes it, and you are told when it does."
          />
          <Upload slug={slug} data={rules.data} />
          <Waiting slug={slug} data={rules.data} />
        </Stack>
      )}
    </>
  );
}
