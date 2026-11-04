/**
 * A draft of a legal text in the editor (docs/legal-texts-in-portal-plan.md,
 * "The editor"), for Admin › Legal texts (/admin/legal/drafts/<id>) and a
 * team's leads (/teams/<team>/manage/rules/drafts/<id>).
 *
 * The title and revision as fields, the Markdown in CodeMirror, German and
 * English as tabs; beside it the page as it will be -- rendered and checked
 * by the server half a second after typing stops -- or the changes against
 * the version in force. A draft saves itself two seconds after typing stops;
 * a save over somebody else's is refused and said. Undo and redo; *Discard
 * changes* puts back the draft as it was when the editor opened, *History*
 * an earlier state -- both as a change undo takes back. Correcting a
 * published version: the same, saved with a button instead.
 *
 * Data: POST /api/v1/legal/drafts/<id>/session, PUT .../<id>, POST .../check,
 * GET .../snapshots/<id>, POST .../correct (aeronautics_members/api/legal_editor.py).
 */
import {
  ActionIcon,
  Alert,
  Button,
  Group,
  List,
  Modal,
  Popover,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Tabs,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { useDebouncedValue, useMediaQuery } from '@mantine/hooks';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import type { EditorView } from '@codemirror/view';
import {
  IconArrowBackUp,
  IconArrowForwardUp,
  IconBold,
  IconHeading,
  IconHelp,
  IconHistory,
  IconItalic,
  IconLink,
  IconList,
  IconListNumbers,
} from '@tabler/icons-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';

import { api, ApiError, call, type Schemas } from '../../api/client';
import { ConfirmButton } from '../../components/ConfirmButton';
import { Changes } from '../../components/legal/Changes';
import textClasses from '../../components/legal/LegalText.module.css';
import { Problems } from '../../components/legal/LegalFiles';
import {
  MarkdownEditor,
  prefixLines,
  redoIn,
  replaceText,
  undoIn,
  wrap,
} from '../../components/legal/MarkdownEditor';
import { PageHeader } from '../../components/PageHeader';
import { PdfLink } from '../../components/PdfLink';
import { Pill } from '../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../components/States';
import { formatDateTime } from '../../lib/format';
import { notifyDone, notifyFailed } from '../../lib/notify';
import { Publish } from '../admin/LegalTexts';
import classes from './Editor.module.css';

type EditorOut = Schemas['EditorOut'];
type Language = 'de' | 'en';
interface Part {
  title: string;
  revision: string | null;
  body: string;
}
interface Content {
  german: Part;
  english: Part | null;
}
type Saving = 'saved' | 'unsaved' | 'saving' | 'conflict' | 'failed';

const SAVE_AFTER_MS = 2000;
const CHECK_AFTER_MS = 500;

export function Editor() {
  const { recordId = '' } = useParams();
  const id = Number(recordId);
  const session = useQuery({
    queryKey: ['legal', 'editor', id] as const,
    queryFn: () =>
      call(api.POST('/api/v1/legal/drafts/{record_id}/session', { params: { path: { record_id: id } } })),
    // One session per visit: opening the editor again keeps the state it finds.
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  });
  if (session.isPending) return <LoadingState />;
  if (session.isError) return <ErrorState error={session.error} onRetry={() => void session.refetch()} />;
  return <Workspace key={id} data={session.data} />;
}

function partOf(text: Schemas['TextOut'] | null | undefined): Part | null {
  return text ? { title: text.title, revision: text.revision, body: text.body } : null;
}

function Workspace({ data }: { data: EditorOut }) {
  const navigate = useNavigate();
  const wide = useMediaQuery('(min-width: 62em)', true);
  const correcting = data.mode === 'correcting';
  const [german, setGerman] = useState<Part>(
    () => partOf(data.german) ?? { title: '', revision: null, body: '' },
  );
  const [english, setEnglish] = useState<Part | null>(() => partOf(data.english));
  const [language, setLanguage] = useState<Language>('de');
  const [pane, setPane] = useState<'write' | 'preview' | 'changes'>('write');
  const [saving, setSaving] = useState<Saving>('saved');
  const [editedAt, setEditedAt] = useState(data.edited_at ?? null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const germanView = useRef<EditorView | null>(null);
  const englishView = useRef<EditorView | null>(null);
  // Only in handlers: a view is never read while drawing.
  const viewOf = (which: Language) => (which === 'de' ? germanView.current : englishView.current);

  // What the saver sends: always the latest, whatever render it runs from.
  const latest = useRef<Content>({ german, english });
  useLayoutEffect(() => {
    latest.current = { german, english };
  }, [german, english]);
  const editCount = useRef(data.edit_count);
  const changes = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const inFlight = useRef(false);
  const saveRef = useRef<() => Promise<void>>(() => Promise.resolve());

  const save = useCallback(async () => {
    window.clearTimeout(timer.current);
    if (correcting || inFlight.current) return;
    inFlight.current = true;
    const startedAt = changes.current;
    setSaving('saving');
    try {
      const out = await call(
        api.PUT('/api/v1/legal/drafts/{record_id}', {
          params: { path: { record_id: data.record_id } },
          body: { ...latest.current, edit_count: editCount.current },
        }),
      );
      editCount.current = out.edit_count;
      setEditedAt(out.edited_at ?? null);
      setSaving(changes.current === startedAt ? 'saved' : 'unsaved');
      if (changes.current !== startedAt) {
        timer.current = window.setTimeout(() => void saveRef.current(), SAVE_AFTER_MS);
      }
    } catch (error) {
      setSaving(error instanceof ApiError && error.code === 'legal_draft_changed' ? 'conflict' : 'failed');
      if (!(error instanceof ApiError && error.code === 'legal_draft_changed')) notifyFailed(error);
    } finally {
      inFlight.current = false;
    }
  }, [correcting, data.record_id]);
  useLayoutEffect(() => {
    saveRef.current = save;
  }, [save]);

  const changed = useCallback(() => {
    changes.current += 1;
    setSaving((now) => (now === 'conflict' ? now : 'unsaved'));
    if (correcting) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void saveRef.current(), SAVE_AFTER_MS);
  }, [correcting]);

  // Leaving with something unsaved: the browser asks; leaving within the app saves it first.
  const unsaved = saving === 'unsaved' || saving === 'saving' || saving === 'failed';
  useEffect(() => {
    if (!unsaved) return undefined;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => {
      window.removeEventListener('beforeunload', warn);
    };
  }, [unsaved]);
  useEffect(
    () => () => {
      if (timer.current !== undefined && changes.current > 0) void saveRef.current();
    },
    [],
  );

  const setPart = (which: Language, part: Partial<Part>) => {
    if (which === 'de') setGerman((now) => ({ ...now, ...part }));
    else setEnglish((now) => (now ? { ...now, ...part } : now));
    changed();
  };

  /** An earlier state put back: the texts as changes that undo takes back. */
  const putBack = (content: Content) => {
    setGerman((now) => ({ ...now, title: content.german.title, revision: content.german.revision }));
    replaceText(germanView.current, content.german.body);
    if (content.english) {
      const englishPart = content.english;
      if (englishView.current) {
        setEnglish((now) => ({
          ...(now ?? englishPart),
          title: englishPart.title,
          revision: englishPart.revision,
        }));
        replaceText(englishView.current, englishPart.body);
      } else {
        setEnglish(englishPart);
      }
    } else {
      setEnglish(null);
      setLanguage('de');
    }
    changed();
  };
  const loadState = useMutation({
    mutationFn: (snapshotId: number) =>
      call(
        api.GET('/api/v1/legal/drafts/{record_id}/snapshots/{snapshot_id}', {
          params: { path: { record_id: data.record_id, snapshot_id: snapshotId } },
        }),
      ),
    onSuccess: (content, snapshotId) => {
      putBack({ german: partOf(content.german) ?? german, english: partOf(content.english) });
      setHistoryOpen(false);
      notifyDone(
        snapshotId === data.opened_snapshot_id
          ? 'Changes discarded. Undo brings them back.'
          : 'Earlier state put back. Undo takes it back.',
      );
    },
    onError: notifyFailed,
  });

  const correct = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/legal/drafts/{record_id}/correct', {
          params: { path: { record_id: data.record_id } },
          body: latest.current,
        }),
      ),
    onSuccess: () => {
      notifyDone('Corrected.');
      setSaving('saved');
      void navigate(data.back_url);
    },
    onError: notifyFailed,
  });

  const active = language === 'de' ? german : english;
  const inForce = data.in_force ? (language === 'de' ? data.in_force.german : data.in_force.english) : null;

  const editor = (
    <Stack gap="sm">
      <Tabs
        value={language}
        onChange={(value) => {
          setLanguage(value === 'en' ? 'en' : 'de');
        }}
      >
        <Tabs.List>
          <Tabs.Tab value="de">German</Tabs.Tab>
          <Tabs.Tab value="en">English{english ? '' : ' (none)'}</Tabs.Tab>
        </Tabs.List>
      </Tabs>
      {active ? (
        <>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
            <TextInput
              label="Title"
              required
              maxLength={200}
              value={active.title}
              onChange={(event) => {
                setPart(language, { title: event.currentTarget.value });
              }}
            />
            <TextInput
              label="Revision (optional)"
              placeholder="Version 2026.2"
              maxLength={100}
              value={active.revision ?? ''}
              onChange={(event) => {
                setPart(language, { revision: event.currentTarget.value || null });
              }}
            />
          </SimpleGrid>
          <Toolbar
            view={() => viewOf(language)}
            onUndo={() => {
              undoIn(viewOf(language));
            }}
            onRedo={() => {
              redoIn(viewOf(language));
            }}
          />
        </>
      ) : null}
      <div hidden={language !== 'de'}>
        <MarkdownEditor
          initial={german.body}
          viewRef={germanView}
          label="German text, Markdown"
          lang="de"
          onChange={(body) => {
            setPart('de', { body });
          }}
        />
      </div>
      {english ? (
        <div hidden={language !== 'en'}>
          <MarkdownEditor
            initial={english.body}
            viewRef={englishView}
            label="English text, Markdown"
            lang="en"
            onChange={(body) => {
              setPart('en', { body });
            }}
          />
          <Group justify="flex-end" mt="xs">
            <ConfirmButton
              size="xs"
              confirmLabel="Yes, remove it"
              onConfirm={() => {
                setEnglish(null);
                setLanguage('de');
                changed();
              }}
            >
              Remove the translation
            </ConfirmButton>
          </Group>
        </div>
      ) : language === 'en' ? (
        <Stack gap="sm" align="flex-start" py="md">
          <Text size="sm" c="dimmed">
            No English translation. Where there is none, the German text is shown.
          </Text>
          <Button
            variant="default"
            onClick={() => {
              setEnglish({ title: '', revision: german.revision, body: '' });
              changed();
            }}
          >
            Add an English translation
          </Button>
        </Stack>
      ) : null}
    </Stack>
  );

  const preview = active ? <Preview recordId={data.record_id} language={language} part={active} /> : null;
  const comparison = active ? (
    inForce ? (
      <Stack gap="xs">
        <Text size="sm" c="dimmed">
          Against the version in force ({data.in_force?.validity}).
        </Text>
        <Changes before={inForce.body} after={active.body} />
      </Stack>
    ) : (
      <EmptyState>{`Nothing in force to compare with${language === 'en' ? ' in English' : ''}.`}</EmptyState>
    )
  ) : null;

  return (
    <>
      <PageHeader
        title={data.name}
        description={
          correcting
            ? 'Correcting the version in force: saved with the button, within 24 hours of publishing.'
            : 'A draft: it saves itself, and nobody else sees it until it is published.'
        }
        crumbs={
          data.back_url.startsWith('/admin')
            ? [
                { label: 'Admin', to: '/admin' },
                { label: 'Legal texts', to: data.back_url },
                { label: data.name },
              ]
            : [
                { label: data.team_name ?? 'Team', to: `/teams/${data.team ?? ''}` },
                { label: 'Rules', to: data.back_url },
                { label: data.name },
              ]
        }
        actions={
          <Group gap="sm">
            <SaveState saving={saving} editedAt={editedAt} correcting={correcting} />
            <Button
              variant="default"
              loading={loadState.isPending && loadState.variables === data.opened_snapshot_id}
              onClick={() => {
                loadState.mutate(data.opened_snapshot_id);
              }}
            >
              Discard changes
            </Button>
            <Button
              variant="default"
              leftSection={<IconHistory size={16} />}
              onClick={() => {
                setHistoryOpen(true);
              }}
            >
              History
            </Button>
            {saving === 'saved' ? <PdfLink href={data.pdf_url}>Preview PDF</PdfLink> : null}
            {correcting ? (
              <ConfirmButton
                confirmLabel="Yes, replace the text"
                loading={correct.isPending}
                disabled={saving === 'saved'}
                onConfirm={() => {
                  correct.mutate();
                }}
              >
                Save correction
              </ConfirmButton>
            ) : data.may_publish ? (
              <Button
                disabled={saving !== 'saved'}
                onClick={() => {
                  setPublishing(true);
                }}
              >
                Publish
              </Button>
            ) : null}
          </Group>
        }
      />
      {saving === 'conflict' ? (
        <Alert color="red" variant="light" title="Somebody else saved this draft meanwhile" mb="md">
          Your changes since are not saved. Copy what you need, then reload the page to see theirs.
        </Alert>
      ) : null}
      {wide ? (
        <div className={classes.split}>
          {editor}
          <Tabs defaultValue="preview" className={classes.side}>
            <Tabs.List>
              <Tabs.Tab value="preview">Preview</Tabs.Tab>
              <Tabs.Tab value="changes">Changes</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="preview" pt="md">
              {preview}
            </Tabs.Panel>
            <Tabs.Panel value="changes" pt="md">
              {comparison}
            </Tabs.Panel>
          </Tabs>
        </div>
      ) : (
        <Stack gap="md">
          <SegmentedControl
            fullWidth
            value={pane}
            onChange={(value) => {
              setPane(value === 'preview' ? 'preview' : value === 'changes' ? 'changes' : 'write');
            }}
            data={[
              { value: 'write', label: 'Write' },
              { value: 'preview', label: 'Preview' },
              { value: 'changes', label: 'Changes' },
            ]}
          />
          <div hidden={pane !== 'write'}>{editor}</div>
          {pane === 'preview' ? preview : null}
          {pane === 'changes' ? comparison : null}
        </Stack>
      )}
      <Modal
        opened={historyOpen}
        onClose={() => {
          setHistoryOpen(false);
        }}
        title="Earlier states"
      >
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            The draft as it was when an editing session started. Putting one back is a change like any other:
            undo takes it back.
          </Text>
          <ul className={classes.history}>
            {data.snapshots.map((snapshot) => (
              <li key={snapshot.id}>
                <div>
                  <Text size="sm">{formatDateTime(snapshot.created_at)}</Text>
                  <Text size="xs" c="dimmed">
                    {snapshot.id === data.opened_snapshot_id
                      ? 'When you opened it'
                      : (snapshot.created_by ?? '')}
                  </Text>
                </div>
                <Button
                  size="xs"
                  variant="default"
                  loading={loadState.isPending && loadState.variables === snapshot.id}
                  onClick={() => {
                    loadState.mutate(snapshot.id);
                  }}
                >
                  Put back
                </Button>
              </li>
            ))}
          </ul>
        </Stack>
      </Modal>
      {publishing ? (
        <Publish
          text={{
            title: german.title,
            record_id: data.record_id,
            effective_from: data.effective_from,
            approved_by: data.approved_by,
            approved_on: data.approved_on,
          }}
          onClose={() => {
            setPublishing(false);
          }}
          onPublished={() => {
            void navigate(data.back_url);
          }}
        />
      ) : null}
    </>
  );
}

function SaveState({
  saving,
  editedAt,
  correcting,
}: {
  saving: Saving;
  editedAt: string | null;
  correcting: boolean;
}) {
  if (saving === 'saving') return <Pill tone="neutral">Saving…</Pill>;
  if (saving === 'conflict') return <Pill tone="failed">Not saved</Pill>;
  if (saving === 'failed') return <Pill tone="pending">Not saved yet</Pill>;
  if (saving === 'unsaved') return <Pill tone="neutral">{correcting ? 'Not saved' : 'Changed'}</Pill>;
  return <Pill tone="active">{editedAt ? `Saved ${formatDateTime(editedAt)}` : 'Saved'}</Pill>;
}

function Toolbar({
  view,
  onUndo,
  onRedo,
}: {
  /** The view written in, asked for on a click. */
  view: () => EditorView | null;
  onUndo: () => void;
  onRedo: () => void;
}) {
  const tool = (label: string, icon: React.ReactNode, run: () => void) => (
    <Tooltip label={label} key={label}>
      <ActionIcon variant="default" size="lg" aria-label={label} onClick={run}>
        {icon}
      </ActionIcon>
    </Tooltip>
  );
  return (
    <Group gap={4} wrap="wrap" role="toolbar" aria-label="Formatting">
      {tool('Undo', <IconArrowBackUp size={18} />, onUndo)}
      {tool('Redo', <IconArrowForwardUp size={18} />, onRedo)}
      <span className={classes.divider} />
      {tool('Section heading (## § )', <IconHeading size={18} />, () => {
        prefixLines(view(), '## § ');
      })}
      {tool('Bold', <IconBold size={18} />, () => {
        wrap(view(), '**');
      })}
      {tool('Italic', <IconItalic size={18} />, () => {
        wrap(view(), '*');
      })}
      {tool('List', <IconList size={18} />, () => {
        prefixLines(view(), '- ');
      })}
      {tool('Numbered list', <IconListNumbers size={18} />, () => {
        prefixLines(view(), '1. ');
      })}
      {tool('Link', <IconLink size={18} />, () => {
        wrap(view(), '[', '](https://)');
      })}
      <Popover width={320} position="bottom-start" shadow="md">
        <Popover.Target>
          <ActionIcon variant="default" size="lg" aria-label="Markdown help">
            <IconHelp size={18} />
          </ActionIcon>
        </Popover.Target>
        <Popover.Dropdown>
          <List size="sm" spacing={4}>
            <List.Item>
              <code>## § 1 Name</code>: a section, in the contents; <code>### …</code> below it
            </List.Item>
            <List.Item>
              <code># Teil B</code>: a part of a long text
            </List.Item>
            <List.Item>
              <code>**bold**</code>, <code>*italic*</code>
            </List.Item>
            <List.Item>
              <code>- point</code> or <code>1. point</code>: lists
            </List.Item>
            <List.Item>
              <code>[text](https://…)</code>: a link; <code>[§ 2](#paragraph-2)</code> to a section
            </List.Item>
            <List.Item>
              An empty line between paragraphs; two spaces at a line&apos;s end for a new line
            </List.Item>
            <List.Item>
              <code>| A | B |</code>, then <code>|---|---|</code>: a table
            </List.Item>
          </List>
        </Popover.Dropdown>
      </Popover>
    </Group>
  );
}

/** The page as it will be, and what keeps it from being published: from the server, as written. */
function Preview({ recordId, language, part }: { recordId: number; language: Language; part: Part }) {
  const [written] = useDebouncedValue(part, CHECK_AFTER_MS);
  const checked = useQuery({
    queryKey: ['legal', 'editor', recordId, 'check', language, written] as const,
    queryFn: () =>
      call(
        api.POST('/api/v1/legal/drafts/{record_id}/check', {
          params: { path: { record_id: recordId } },
          body: { language, ...written },
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
  if (checked.isPending) return <LoadingState />;
  if (checked.isError) return <ErrorState error={checked.error} onRetry={() => void checked.refetch()} />;
  return (
    <Stack gap="md">
      {checked.data.problems.length ? (
        <Problems problems={checked.data.problems} title="Cannot be published like this:" />
      ) : (
        <Text size="sm" c="var(--ja-success-text)">
          Nothing in the way of publishing it.
        </Text>
      )}
      <h2 className={classes.previewTitle} lang={language}>
        {written.title}
      </h2>
      <div
        className={textClasses.text}
        lang={language}
        // Markdown rendered on the server with HTML switched off: nothing in it can run.
        dangerouslySetInnerHTML={{ __html: checked.data.html }}
      />
    </Stack>
  );
}
