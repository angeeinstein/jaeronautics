/**
 * Legal texts (docs/frontend-structure.md, 4; docs/legal-texts-in-portal-plan.md):
 * a new version uploaded as a draft -- or only previewed as its PDF, kept
 * nowhere -- and published with the day it applies from and who approved
 * it; the texts in force, a correction within 24 hours while nobody has
 * accepted it, the versions not in force yet, making every PDF again, and
 * what is wrong with the files in legal/ (the versions from before, which
 * stay as they are). Data: GET /api/v1/admin/legal
 * (aeronautics_members/api/admin_legal.py).
 */
import {
  Anchor,
  Button,
  Checkbox,
  Group,
  List,
  Modal,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { IconClock, IconFileTypePdf, IconFolder, IconScale, IconUpload } from '@tabler/icons-react';

import { api, ApiError, call, type Schemas } from '../../api/client';
import { Checklist } from '../../components/Checklist';
import { ConfirmButton } from '../../components/ConfirmButton';
import { DayInput } from '../../components/DayInput';
import {
  formOf,
  LegalFiles,
  NO_FILES,
  Problems,
  problemsOf,
  type ChosenFiles,
} from '../../components/legal/LegalFiles';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { PdfLink } from '../../components/PdfLink';
import { Pill } from '../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { fileNameOf, openFile } from '../../lib/files';
import { formatDate, formatDateTime, viennaParts } from '../../lib/format';
import { notifyDone, notifyFailed } from '../../lib/notify';
import { useEditDraft, WriteNew } from '../legal/Starting';
import classes from './LegalTexts.module.css';

type LegalOut = Schemas['LegalOut'];
type Waiting = LegalOut['waiting'][number];
type InForceText = LegalOut['in_force'][number];

const legalQuery = {
  queryKey: ['admin', 'legal'] as const,
  queryFn: () => call(api.GET('/api/v1/admin/legal')),
};

function useRefresh() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: legalQuery.queryKey });
}

function today() {
  return viennaParts(new Date().toISOString()).day;
}

function NewVersion({ data }: { data: LegalOut }) {
  const refresh = useRefresh();
  const [files, setFiles] = useState<ChosenFiles>(NO_FILES);
  const [problems, setProblems] = useState<string[]>([]);
  const failed = (error: Error) => {
    const listed = problemsOf(error);
    if (listed) setProblems(listed);
    else notifyFailed(error);
  };
  const preview = useMutation({
    mutationFn: async () => {
      const {
        data: pdf,
        error,
        response,
      } = await api.POST('/api/v1/admin/legal/preview', { body: formOf(files), parseAs: 'blob' });
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
    onError: failed,
  });
  const upload = useMutation({
    mutationFn: () => call(api.POST('/api/v1/admin/legal/drafts', { body: formOf(files) })),
    onSuccess: async () => {
      setProblems([]);
      setFiles(NO_FILES);
      notifyDone('Uploaded as a draft.');
      await refresh();
    },
    onError: failed,
  });

  return (
    <Panel icon={IconUpload} title="Upload a file">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          The German Markdown file of a new version -- the association&apos;s or a team&apos;s rules; its
          English is made by machine once it is published. Checked as a file in legal/ is. A draft is seen
          only here until it is published; the preview keeps nothing.
        </Text>
        <Problems problems={problems} title="Not taken, because of:" />
        <LegalFiles
          value={files}
          onChange={(chosen) => {
            setFiles(chosen);
            setProblems([]);
          }}
        />
        <Text size="sm" c="dimmed">
          At most {data.preview_max_kb} KB each; the file&apos;s name does not matter. A team&apos;s rules
          carry its short name in the front matter (team: &quot;rocket-team&quot;) and get its logo.
        </Text>
        <Group gap="md">
          <Button
            loading={upload.isPending}
            disabled={!files.german || preview.isPending}
            onClick={() => {
              upload.mutate();
            }}
          >
            Upload as draft
          </Button>
          <Button
            variant="default"
            loading={preview.isPending}
            disabled={!files.german || upload.isPending}
            onClick={() => {
              preview.mutate();
            }}
          >
            Preview PDF
          </Button>
          <Anchor href={data.template_url} download>
            Download a template
          </Anchor>
          <Text size="sm" c="dimmed" className={classes.hint}>
            The structure a text needs and everything it can do, dated today.
          </Text>
        </Group>
      </Stack>
    </Panel>
  );
}

/** A published version's wording replaced: within 24 hours, while nobody has accepted it. */
function Correct({ text, onClose }: { text: InForceText; onClose: () => void }) {
  const refresh = useRefresh();
  const [files, setFiles] = useState<ChosenFiles>(NO_FILES);
  const [problems, setProblems] = useState<string[]>([]);
  const correct = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/admin/legal/versions/{record_id}/correct', {
          params: { path: { record_id: text.record_id ?? 0 } },
          body: formOf(files),
        }),
      ),
    onSuccess: async () => {
      notifyDone('Corrected.');
      onClose();
      await refresh();
    },
    onError: (error) => {
      const listed = problemsOf(error);
      if (listed) setProblems(listed);
      else notifyFailed(error);
    },
  });
  return (
    <Modal opened onClose={onClose} title={`Correct: ${text.title}`} size="lg">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          The whole German file again, with the mistake put right. Its day, the day it applies from and its
          approval stay; the earlier wording is kept in the log, and the English is made anew.
        </Text>
        <Problems problems={problems} title="Not corrected, because of:" />
        <LegalFiles
          value={files}
          onChange={(chosen) => {
            setFiles(chosen);
            setProblems([]);
          }}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <ConfirmButton
            confirmLabel="Yes, replace the text"
            disabled={!files.german}
            loading={correct.isPending}
            onConfirm={() => {
              correct.mutate();
            }}
          >
            Correct
          </ConfirmButton>
        </Group>
      </Stack>
    </Modal>
  );
}

function InForce({ data }: { data: LegalOut }) {
  const navigate = useNavigate();
  const [correcting, setCorrecting] = useState<InForceText | null>(null);
  return (
    <Panel icon={IconScale} title="In force" flush>
      <ul className={classes.list}>
        {data.in_force.map((text) => (
          <li key={text.page_url} className={classes.item}>
            <Stack gap={2}>
              <Anchor href={text.page_url}>{text.title}</Anchor>
              <Text size="sm" c="dimmed">
                {text.team_name ? `${text.team_name} · ` : ''}Version of {formatDate(text.version)}
              </Text>
              <Text size="sm" c="dimmed" lang="de">
                {text.validity}
              </Text>
            </Stack>
            <Group gap="sm">
              {text.correctable && data.may_publish && text.record_id != null ? (
                <>
                  <Button
                    size="xs"
                    variant="default"
                    onClick={() => {
                      void navigate(`/admin/legal/drafts/${String(text.record_id)}`);
                    }}
                  >
                    Correct
                  </Button>
                  <Button
                    size="xs"
                    variant="subtle"
                    onClick={() => {
                      setCorrecting(text);
                    }}
                  >
                    With a file
                  </Button>
                </>
              ) : null}
              <PdfLink href={text.pdf_url}>PDF</PdfLink>
            </Group>
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
      {data.may_publish && data.in_force.some((text) => text.correctable) ? (
        <Text size="sm" c="dimmed" className={classes.note}>
          A version published here can be corrected for 24 hours, while nobody has accepted it.
        </Text>
      ) : null}
      {correcting ? (
        <Correct
          text={correcting}
          onClose={() => {
            setCorrecting(null);
          }}
        />
      ) : null}
    </Panel>
  );
}

const APPROVERS = ['Vorstand', 'Generalversammlung'];

/** Publishing a draft: the day it applies from, and who approved it and when. Not to be undone. */
type Publishable = Pick<
  Waiting,
  | 'title'
  | 'record_id'
  | 'effective_from'
  | 'approved_by'
  | 'approved_on'
  | 'tells_by_default'
  | 'members_to_tell'
  | 'team_name'
>;

export function Publish({
  text,
  onClose,
  onPublished,
}: {
  text: Publishable;
  onClose: () => void;
  /** After publishing: the editor goes back to the list. */
  onPublished?: () => void;
}) {
  const refresh = useRefresh();
  const first = today();
  const [effectiveFrom, setEffectiveFrom] = useState(
    text.effective_from > first ? text.effective_from : first,
  );
  const named = text.approved_by ?? '';
  const [approver, setApprover] = useState(!named ? 'none' : APPROVERS.includes(named) ? named : 'other');
  const [other, setOther] = useState(APPROVERS.includes(named) ? '' : named);
  const [approvedOn, setApprovedOn] = useState(text.approved_on ?? '');
  const [tell, setTell] = useState(Boolean(text.tells_by_default));
  const [summary, setSummary] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<string[]>([]);
  const approvedBy = approver === 'none' ? null : approver === 'other' ? other.trim() || null : approver;
  const publish = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/admin/legal/drafts/{record_id}/publish', {
          params: { path: { record_id: text.record_id ?? 0 } },
          body: {
            effective_from: effectiveFrom,
            approved_by: approvedBy,
            approved_on: approvedBy || approvedOn ? approvedOn || null : null,
            tell,
            summary: tell ? summary.trim() || null : null,
          },
        }),
      ),
    onSuccess: async () => {
      notifyDone('Published.');
      onClose();
      onPublished?.();
      await refresh();
    },
    onError: (error) => {
      const listed = problemsOf(error);
      if (listed) setProblems(listed);
      else if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  return (
    <Modal opened onClose={onClose} title={`Publish: ${text.title}`} size="lg">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Published, it is a version for good: from the day it applies, applicants accept it and members see
          that it changed. For 24 hours, while nobody has accepted it, its wording can still be corrected.
        </Text>
        <Problems problems={problems} title="Not published, because of:" />
        <DayInput
          label="Applies from"
          required
          minDate={first}
          value={effectiveFrom}
          onChange={setEffectiveFrom}
          error={errors.effective_from}
        />
        <Stack gap={6}>
          <Text size="sm" fw={500}>
            Approved by
          </Text>
          <SegmentedControl
            value={approver}
            onChange={setApprover}
            data={[
              { value: 'none', label: 'Nobody named' },
              { value: 'Vorstand', label: 'Vorstand' },
              { value: 'Generalversammlung', label: 'Generalversammlung' },
              { value: 'other', label: 'Other' },
            ]}
          />
          {approver === 'other' ? (
            <TextInput
              aria-label="Approved by"
              placeholder="Who approved it"
              maxLength={100}
              value={other}
              onChange={(event) => {
                setOther(event.currentTarget.value);
              }}
              error={errors.approved_by}
            />
          ) : null}
        </Stack>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <DayInput
            label="Approved on"
            clearable
            maxDate={first}
            value={approvedOn}
            onChange={setApprovedOn}
            error={errors.approved_on}
          />
        </SimpleGrid>
        <Text size="sm" c="dimmed">
          Members see: Gültig ab {effectiveFrom ? formatDate(effectiveFrom) : '…'}
          {approvedBy || approvedOn ? ' · genehmigt' : ''}
          {approvedBy === 'Vorstand' ? ' vom Vorstand' : ''}
          {approvedBy === 'Generalversammlung' ? ' von der Generalversammlung' : ''}
          {approvedOn ? ` am ${formatDate(approvedOn)}` : ''}
          {approvedBy && !APPROVERS.includes(approvedBy) ? ` (${approvedBy})` : ''}
        </Text>
        <Stack gap={6}>
          <Checkbox
            label={`Tell the members by email (${String(text.members_to_tell ?? 0)}${
              text.team_name ? `, the members of ${text.team_name}` : ', all active members'
            })`}
            description="Told, not asked: from the day it applies, this version applies to everybody."
            checked={tell}
            onChange={(event) => {
              setTell(event.currentTarget.checked);
            }}
          />
          {tell ? (
            <Textarea
              label="What changed (optional)"
              description="A few lines for the email, beside the link to the text."
              autosize
              minRows={2}
              maxLength={2000}
              value={summary}
              onChange={(event) => {
                setSummary(event.currentTarget.value);
              }}
              error={errors.summary}
            />
          ) : null}
        </Stack>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <ConfirmButton
            confirmLabel="Yes, publish"
            disabled={!effectiveFrom}
            loading={publish.isPending}
            onConfirm={() => {
              publish.mutate();
            }}
          >
            Publish
          </ConfirmButton>
        </Group>
      </Stack>
    </Modal>
  );
}

function NotInForce({ data }: { data: LegalOut }) {
  const refresh = useRefresh();
  const edit = useEditDraft((recordId) => `/admin/legal/drafts/${String(recordId)}`);
  const [publishing, setPublishing] = useState<Waiting | null>(null);
  const remove = useMutation({
    mutationFn: (recordId: number) =>
      call(
        api.DELETE('/api/v1/admin/legal/drafts/{record_id}', { params: { path: { record_id: recordId } } }),
      ),
    onSuccess: async () => {
      notifyDone('Draft deleted.');
      await refresh();
    },
    onError: notifyFailed,
  });
  return (
    <Panel icon={IconClock} title="Not in force yet" flush>
      {data.waiting.length ? (
        <>
          <ul className={classes.list}>
            {data.waiting.map((text) => {
              // A draft uploaded here; one in legal/ is published in the repository.
              const draftId = text.status === 'draft' ? (text.record_id ?? null) : null;
              return (
                <li key={text.pdf_url} className={classes.item}>
                  <Stack gap={4}>
                    <Text>{text.title}</Text>
                    <Group gap="xs">
                      <Text size="sm" c="dimmed">
                        {text.team_name ? `${text.team_name} · ` : ''}
                        {draftId !== null ? 'Uploaded' : `Version of ${formatDate(text.version)}`}
                        {text.uploaded_at ? ` ${formatDateTime(text.uploaded_at)}` : ''}
                        {text.uploaded_by ? ` by ${text.uploaded_by}` : ''}
                        {text.revision ? ` · ${text.revision}` : ''}
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
                          edit.mutate(text);
                        }}
                      >
                        Edit
                      </Button>
                    ) : null}
                    {draftId !== null ? (
                      <>
                        {data.may_publish ? (
                          <Button
                            size="xs"
                            onClick={() => {
                              setPublishing(text);
                            }}
                          >
                            Publish
                          </Button>
                        ) : null}
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
                      </>
                    ) : null}
                    {text.markdown_url ? (
                      <Anchor href={text.markdown_url} download size="sm">
                        Markdown
                      </Anchor>
                    ) : null}
                    <PdfLink href={text.pdf_url}>Preview PDF</PdfLink>
                  </Group>
                </li>
              );
            })}
          </ul>
          <Text size="sm" c="dimmed" className={classes.note}>
            Drafts are shown nowhere else; published versions with a later start day switch over by themselves
            on that day. The preview is marked ENTWURF or VORSCHAU on every page. A draft from legal/ is
            copied into the portal when it is first edited.
          </Text>
        </>
      ) : (
        <div className={classes.empty}>
          <EmptyState>No drafts, and no versions waiting for their start day.</EmptyState>
        </div>
      )}
      {publishing ? (
        <Publish
          text={publishing}
          onClose={() => {
            setPublishing(null);
          }}
        />
      ) : null}
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
    <Panel icon={IconFileTypePdf} title="PDFs">
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
    <Panel icon={IconFolder} title="The files on this server">
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
        description="The association's texts and each team's rules: new versions, published here."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Legal texts' }]}
      />
      {legal.isPending ? (
        <LoadingState />
      ) : legal.isError ? (
        <ErrorState error={legal.error} onRetry={() => void legal.refetch()} />
      ) : (
        <Stack gap="lg">
          <WriteNew
            texts={legal.data.texts ?? []}
            intro="In the editor, with the page beside it as it will be. Saved as a draft as you write; nobody else sees it until it is published."
          />
          <NewVersion data={legal.data} />
          <InForce data={legal.data} />
          <NotInForce data={legal.data} />
          <Remake data={legal.data} />
          <Files data={legal.data} />
        </Stack>
      )}
    </>
  );
}
