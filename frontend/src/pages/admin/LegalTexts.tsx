/**
 * Legal texts (docs/frontend-structure.md, 4): a new text's PDF from its
 * uploaded Markdown file -- checked as the build checks it, kept nowhere --
 * then the texts in force, the versions not in force yet, making every PDF
 * again, and what is wrong with the files on this server. The texts are
 * files in legal/ in the repository; nothing here changes them. Data:
 * GET /api/v1/admin/legal (aeronautics_members/api/admin_legal.py).
 */
import { Alert, Anchor, Button, FileInput, Group, List, SimpleGrid, Stack, Text } from '@mantine/core';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../api/client';
import { Checklist } from '../../components/Checklist';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { PdfLink } from '../../components/PdfLink';
import { Pill } from '../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { fileNameOf, openFile } from '../../lib/files';
import { formatDate } from '../../lib/format';
import { notifyFailed } from '../../lib/notify';
import classes from './LegalTexts.module.css';

type LegalOut = Schemas['LegalOut'];

const legalQuery = {
  queryKey: ['admin', 'legal'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/legal')),
};

function Preview({ data }: { data: LegalOut }) {
  const [german, setGerman] = useState<File | null>(null);
  const [english, setEnglish] = useState<File | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const make = useMutation({
    mutationFn: async () => {
      if (!german) throw new Error('Choose the German file.');
      const form = new FormData();
      form.append('german', german);
      if (english) form.append('english', english);
      const {
        data: pdf,
        error,
        response,
      } = await api.POST('/api/v1/admin/legal/preview', {
        // openapi-fetch sends a FormData as it is, with its own Content-Type.
        body: form as unknown as { german: string },
        parseAs: 'blob',
      });
      if (!response.ok || !(pdf instanceof Blob)) {
        // The API's error, as every other call has it.
        await call(Promise.resolve({ data: undefined, error, response }));
        throw new ApiError(response.status, 'unexpected', 'The PDF could not be made just now.');
      }
      return { pdf, name: fileNameOf(response, 'VORSCHAU.pdf') };
    },
    onSuccess: ({ pdf, name }) => {
      setProblems([]);
      openFile(pdf, name);
    },
    onError: (error) => {
      const listed = error instanceof ApiError ? error.details.problems : undefined;
      if (Array.isArray(listed)) setProblems(listed.map(String));
      else notifyFailed(error);
    },
  });

  return (
    <Panel title="Preview a new text">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Upload the Markdown file of a new version, the association&apos;s or a team&apos;s rules, and get it
          back as the PDF it will be. It is checked as the build checks it, and nothing is kept. Every page
          says ENTWURF (a draft) or VORSCHAU, so it is never taken for the text in force. To publish it, the
          file goes into legal/ in the repository.
        </Text>
        {problems.length ? (
          <Alert color="amber" variant="light" title="Not laid out, because of:">
            <List size="sm" spacing={2}>
              {problems.map((problem) => (
                <List.Item key={problem}>{problem}</List.Item>
              ))}
            </List>
          </Alert>
        ) : null}
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <FileInput
            label="German file"
            accept=".md,text/markdown,text/plain"
            placeholder="Choose a .md file"
            clearable
            value={german}
            onChange={(file) => {
              setGerman(file);
              setProblems([]);
            }}
          />
          <FileInput
            label="English translation (optional)"
            accept=".md,text/markdown,text/plain"
            placeholder="Choose a .md file"
            clearable
            value={english}
            onChange={(file) => {
              setEnglish(file);
              setProblems([]);
            }}
          />
        </SimpleGrid>
        <Text size="sm" c="dimmed">
          Named after the version, like 2026-10-05.md, at most {data.preview_max_kb} KB each. A team&apos;s
          rules carry its short name in the front matter (team: &quot;rocket-team&quot;) and get that
          team&apos;s logo.
        </Text>
        <Group gap="md">
          <Button
            loading={make.isPending}
            disabled={!german}
            onClick={() => {
              make.mutate();
            }}
          >
            Make the PDF
          </Button>
          <Anchor href={data.template_url} download>
            Download a template
          </Anchor>
          <Text size="sm" c="dimmed" className={classes.hint}>
            The structure a text needs and everything it can do, dated today: preview it as it is to see each
            in the PDF.
          </Text>
        </Group>
      </Stack>
    </Panel>
  );
}

function InForce({ data }: { data: LegalOut }) {
  return (
    <Panel title="In force" flush>
      <ul className={classes.list}>
        {data.in_force.map((text) => (
          <li key={text.page_url} className={classes.item}>
            <Stack gap={2}>
              <Anchor href={text.page_url}>{text.title}</Anchor>
              <Text size="sm" c="dimmed">
                {text.team_name ? `${text.team_name} · ` : ''}Version of {formatDate(text.version)}
                {text.revision ? ` (${text.revision})` : ''} · in force since{' '}
                {formatDate(text.effective_from)}
              </Text>
            </Stack>
            <PdfLink href={text.pdf_url}>PDF</PdfLink>
          </li>
        ))}
        {data.teams_without_rules.map((team) => (
          <li key={team} className={classes.item}>
            <Stack gap={2}>
              <Text>Rules of {team}</Text>
              <Text size="sm" c="dimmed">
                {team} · None in force
              </Text>
            </Stack>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function NotInForce({ data }: { data: LegalOut }) {
  return (
    <Panel title="Not in force yet" flush>
      {data.waiting.length ? (
        <>
          <ul className={classes.list}>
            {data.waiting.map((text) => (
              <li key={text.pdf_url} className={classes.item}>
                <Stack gap={4}>
                  <Text>{text.title}</Text>
                  <Group gap="xs">
                    <Text size="sm" c="dimmed">
                      {text.team_name ? `${text.team_name} · ` : ''}Version of {formatDate(text.version)}
                      {text.revision ? ` (${text.revision})` : ''}
                      {text.has_english ? ' · with English' : ''}
                    </Text>
                    {text.status === 'draft' ? (
                      <Pill tone="neutral">Draft</Pill>
                    ) : (
                      <Pill tone="info">{`From ${formatDate(text.effective_from)}`}</Pill>
                    )}
                  </Group>
                </Stack>
                <PdfLink href={text.pdf_url}>Preview PDF</PdfLink>
              </li>
            ))}
          </ul>
          <Text size="sm" c="dimmed" className={classes.note}>
            Drafts are shown nowhere else; published versions with a later start day switch over by themselves
            on that day. The preview is marked ENTWURF or VORSCHAU on every page.
          </Text>
        </>
      ) : (
        <div className={classes.empty}>
          <EmptyState>No drafts, and no versions waiting for their start day.</EmptyState>
        </div>
      )}
    </Panel>
  );
}

type Step = { state: 'waiting' } | { state: 'running' } | { state: 'ok' | 'failed'; detail: string };

/** "Make all PDFs again": the kept ones removed, then one request per PDF, each ticked off. */
function Remake({ data }: { data: LegalOut }) {
  const lines = [
    { key: 'stored', label: 'Remove the kept PDFs' },
    ...data.pdf_jobs.map((job) => ({
      key: job.key,
      label: `${job.title}${job.team_name ? ` · ${job.team_name}` : ''} · Version of ${formatDate(job.version)}`,
    })),
  ];
  const [steps, setSteps] = useState<Record<string, Step> | null>(null);
  const [running, setRunning] = useState(false);

  async function run() {
    setRunning(true);
    const progress: Record<string, Step> = Object.fromEntries(
      lines.map((line) => [line.key, { state: 'waiting' }]),
    );
    const set = (key: string, step: Step) => {
      progress[key] = step;
      setSteps({ ...progress });
    };
    for (const line of lines) {
      set(line.key, { state: 'running' });
      try {
        if (line.key === 'stored') {
          const { removed } = await call(api.POST('/api/v1/admin/legal/pdfs/forget'));
          set(line.key, { state: 'ok', detail: `${String(removed)} removed` });
        } else {
          const { size_kb } = await call(
            api.POST('/api/v1/admin/legal/pdfs/make', { body: { key: line.key } }),
          );
          set(line.key, { state: 'ok', detail: `${String(size_kb)} KB` });
        }
      } catch (error) {
        set(line.key, {
          state: 'failed',
          detail: error instanceof ApiError ? error.message : 'No answer from the server.',
        });
      }
    }
    setRunning(false);
  }

  return (
    <Panel title="PDFs">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Each PDF is made the first time somebody opens it and kept. A changed text, layout or logo makes a
          new one by itself. This makes them all again now: after an update, say, or when a PDF looks out of
          date.
        </Text>
        <Group>
          <Button variant="default" loading={running} onClick={() => void run()}>
            Make all PDFs again
          </Button>
        </Group>
        {steps ? (
          <Checklist
            label="Making the PDFs"
            items={lines.map((line) => {
              const step = steps[line.key] ?? { state: 'waiting' };
              return {
                key: line.key,
                label: line.label,
                // Not started yet, as against waiting for an answer.
                state: step.state === 'waiting' ? 'pending' : step.state,
                detail: 'detail' in step ? step.detail : null,
              };
            })}
          />
        ) : null}
      </Stack>
    </Panel>
  );
}

function Files({ data }: { data: LegalOut }) {
  return (
    <Panel title="The files on this server">
      {data.problems.length ? (
        <List size="sm" spacing={4}>
          {data.problems.map((problem) => (
            <List.Item key={problem}>{problem}</List.Item>
          ))}
        </List>
      ) : (
        <Text c="var(--ja-success-text)">Every file in legal/ is in order.</Text>
      )}
    </Panel>
  );
}

export function LegalTexts() {
  const legal = useQuery(legalQuery);
  return (
    <>
      <PageHeader
        title="Legal texts"
        description="The texts in force, each team's rules, and a preview of a new text as a PDF."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Legal texts' }]}
      />
      {legal.isPending ? (
        <LoadingState />
      ) : legal.isError ? (
        <ErrorState error={legal.error} onRetry={() => void legal.refetch()} />
      ) : (
        <Stack gap="lg">
          <Preview data={legal.data} />
          <InForce data={legal.data} />
          <NotInForce data={legal.data} />
          <Remake data={legal.data} />
          <Files data={legal.data} />
        </Stack>
      )}
    </>
  );
}
