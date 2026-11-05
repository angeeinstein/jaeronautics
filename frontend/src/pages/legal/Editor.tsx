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
 * One person edits a draft at a time: whoever opens it first holds it, and
 * the editor says so to the server every 30 seconds; anyone else sees it
 * read-only, with who is editing it and since when, and can edit once it is
 * free -- or take it over when its holder has not typed for 10 minutes.
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
  Menu,
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
  IconFileText,
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
import {
  formOf,
  LegalFiles,
  NO_FILES,
  Problems,
  problemsOf,
  type ChosenFiles,
} from '../../components/legal/LegalFiles';
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
import { formatDateTime, formatTime } from '../../lib/format';
import { notifyDone, notifyFailed } from '../../lib/notify';
import { Publish } from '../admin/LegalTexts';
import classes from './Editor.module.css';

type EditorOut = Schemas['EditorOut'];
interface Part {
  title: string;
  revision: string | null;
  body: string;
}
interface Content {
  german: Part;
}
type Saving = 'saved' | 'unsaved' | 'saving' | 'conflict' | 'failed';

const SAVE_AFTER_MS = 2000;
const CHECK_AFTER_MS = 500;
/** The English view asks the translator once typing has stopped this long. */
const TRANSLATE_AFTER_MS = 3000;
/** The holder's sign of life, and the others asking whether it is free. */
const LOCK_EVERY_MS = 30_000;

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
  // Taking the draft over loads it again, as it is now: a new workspace.
  return (
    <Workspace
      key={`${String(id)}-${String(session.dataUpdatedAt)}`}
      data={session.data}
      onReload={() => void session.refetch()}
    />
  );
}

function partOf(text: Schemas['TextOut'] | null | undefined): Part | null {
  return text ? { title: text.title, revision: text.revision, body: text.body } : null;
}

function Workspace({ data, onReload }: { data: EditorOut; onReload: () => void }) {
  const navigate = useNavigate();
  const wide = useMediaQuery('(min-width: 62em)', true);
  const correcting = data.mode === 'correcting';
  const [german, setGerman] = useState<Part>(
    () => partOf(data.german) ?? { title: '', revision: null, body: '' },
  );
  const [pane, setPane] = useState<'write' | 'preview' | 'english' | 'changes'>('write');
  const [side, setSide] = useState<'preview' | 'english' | 'changes'>('preview');
  const [saving, setSaving] = useState<Saving>('saved');
  const [editedAt, setEditedAt] = useState(data.edited_at ?? null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [lock, setLock] = useState(data.lock);
  // Taken over while writing here: by whom.
  const [lostTo, setLostTo] = useState<string | null>(null);
  const readOnly = !lock.mine;
  const germanView = useRef<EditorView | null>(null);

  // What the saver sends: always the latest, whatever render it runs from.
  const latest = useRef<Content>({ german });
  useLayoutEffect(() => {
    latest.current = { german };
  }, [german]);
  const editCount = useRef(data.edit_count);
  const changes = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const inFlight = useRef(false);
  const saveRef = useRef<() => Promise<void>>(() => Promise.resolve());
  const typedSinceBeat = useRef(false);
  const mine = useRef(lock.mine);
  useLayoutEffect(() => {
    mine.current = lock.mine;
  }, [lock.mine]);

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
      if (error instanceof ApiError && error.code === 'legal_draft_locked') {
        // Taken over meanwhile: read-only from now on; the next look says by whom.
        setLostTo('Somebody else');
        setLock((now) => ({ ...now, mine: false }));
        setSaving('unsaved');
        return;
      }
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
    typedSinceBeat.current = true;
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

  // The lock: the holder's sign of life every 30 seconds -- the others asking whether it is free --
  // and leaving frees it at once (else the server frees it a minute later by itself).
  useEffect(() => {
    const path = { params: { path: { record_id: data.record_id } } };
    const ask = async (action: 'take' | 'beat' | 'look') => {
      const typed = typedSinceBeat.current;
      typedSinceBeat.current = false;
      try {
        const now = await call(
          api.POST('/api/v1/legal/drafts/{record_id}/lock', { ...path, body: { action, typed } }),
        );
        if (action !== 'look' && !now.mine) {
          setLostTo(now.holder ?? 'Somebody else');
          window.clearTimeout(timer.current);
        }
        setLock(now);
      } catch {
        // The next one asks again; the server keeps the lock a minute.
      }
    };
    const leave = () => {
      if (mine.current) {
        // Best effort: unanswered, the server frees it a minute later by itself.
        api
          .DELETE('/api/v1/legal/drafts/{record_id}/lock', { ...path, keepalive: true })
          .catch(() => undefined);
      }
    };
    // Held already (taken when the session started): said again, which also takes it back after a remount.
    if (mine.current) void ask('take');
    const every = window.setInterval(() => void ask(mine.current ? 'beat' : 'look'), LOCK_EVERY_MS);
    window.addEventListener('pagehide', leave);
    return () => {
      window.clearInterval(every);
      window.removeEventListener('pagehide', leave);
      leave();
    };
  }, [data.record_id]);

  const take = useMutation({
    mutationFn: (action: 'take' | 'take_over') =>
      call(
        api.POST('/api/v1/legal/drafts/{record_id}/lock', {
          params: { path: { record_id: data.record_id } },
          body: { action, typed: false },
        }),
      ),
    onSuccess: (now) => {
      if (now.mine) {
        notifyDone('You are editing it now.');
        onReload();
      } else {
        setLock(now);
        notifyFailed(new ApiError(409, 'legal_draft_locked', 'Somebody else is editing it already.'));
      }
    },
    onError: notifyFailed,
  });

  const setPart = (part: Partial<Part>) => {
    setGerman((now) => ({ ...now, ...part }));
    changed();
  };

  /** An earlier state put back: the text as a change undo takes back. */
  const putBack = (content: Content) => {
    setGerman((now) => ({ ...now, title: content.german.title, revision: content.german.revision }));
    replaceText(germanView.current, content.german.body);
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
      putBack({ german: partOf(content.german) ?? german });
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

  const inForce = data.in_force?.german ?? null;

  const editor = (
    <Stack gap="sm">
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
        <TextInput
          label="Title"
          required
          readOnly={readOnly}
          maxLength={200}
          value={german.title}
          onChange={(event) => {
            setPart({ title: event.currentTarget.value });
          }}
        />
        <TextInput
          label="Revision (optional)"
          placeholder="Version 2026.2"
          readOnly={readOnly}
          maxLength={100}
          value={german.revision ?? ''}
          onChange={(event) => {
            setPart({ revision: event.currentTarget.value || null });
          }}
        />
      </SimpleGrid>
      <Toolbar
        disabled={readOnly}
        view={() => germanView.current}
        onUndo={() => {
          undoIn(germanView.current);
        }}
        onRedo={() => {
          redoIn(germanView.current);
        }}
      />
      <MarkdownEditor
        initial={german.body}
        viewRef={germanView}
        label="German text, Markdown"
        lang="de"
        readOnly={readOnly}
        onChange={(body) => {
          setPart({ body });
        }}
      />
    </Stack>
  );

  const preview = <Preview recordId={data.record_id} part={german} />;
  const english = <EnglishView recordId={data.record_id} part={german} />;
  const comparison = inForce ? (
    <Stack gap="xs">
      <Text size="sm" c="dimmed">
        Against the version in force ({data.in_force?.validity}).
      </Text>
      <Changes before={inForce.body} after={german.body} />
    </Stack>
  ) : (
    <EmptyState>Nothing in force to compare with.</EmptyState>
  );

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
              disabled={readOnly}
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
            <Menu position="bottom-end" withinPortal>
              <Menu.Target>
                <Button variant="default" leftSection={<IconFileText size={16} />}>
                  File
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>As saved</Menu.Label>
                <Menu.Item component="a" href={data.markdown_url} download disabled={saving !== 'saved'}>
                  Download Markdown
                </Menu.Item>
                <Menu.Divider />
                <Menu.Item
                  disabled={readOnly}
                  onClick={() => {
                    setReplacing(true);
                  }}
                >
                  Replace with a file…
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
            {saving === 'saved' ? <PdfLink href={data.pdf_url}>Preview PDF</PdfLink> : null}
            {correcting ? (
              <ConfirmButton
                confirmLabel="Yes, replace the text"
                loading={correct.isPending}
                disabled={saving === 'saved' || readOnly}
                onConfirm={() => {
                  correct.mutate();
                }}
              >
                Save correction
              </ConfirmButton>
            ) : data.may_publish ? (
              <Button
                disabled={saving !== 'saved' || readOnly}
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
      {readOnly ? (
        <Alert
          color={lostTo ? 'red' : lock.free ? 'brand' : 'yellow'}
          variant="light"
          mb="md"
          title={
            lostTo
              ? `${lostTo} took over this draft`
              : lock.free
                ? 'Nobody is editing this draft now'
                : `${lock.holder ?? 'Somebody'} is editing this draft${lock.since ? ` (since ${formatTime(lock.since)})` : ''}`
          }
        >
          <Group justify="space-between" gap="sm">
            <Text size="sm">
              {lostTo
                ? 'What you wrote until your last save is kept. You see the draft read-only now.'
                : lock.free
                  ? 'Edit it to make changes: it loads as it is now.'
                  : lock.can_take_over
                    ? 'They have not typed for 10 minutes: you may take it over.'
                    : 'You see it read-only. It is free again once they leave the editor.'}
            </Text>
            {lock.free || lock.can_take_over ? (
              <Button
                size="xs"
                loading={take.isPending}
                onClick={() => {
                  take.mutate(lock.free ? 'take' : 'take_over');
                }}
              >
                {lock.free ? 'Edit' : 'Take over'}
              </Button>
            ) : null}
          </Group>
        </Alert>
      ) : null}
      {saving === 'conflict' ? (
        <Alert color="red" variant="light" title="Somebody else saved this draft meanwhile" mb="md">
          Your changes since are not saved. Copy what you need, then reload the page to see theirs.
        </Alert>
      ) : null}
      {wide ? (
        <div className={classes.split}>
          {editor}
          {/* Only the pane shown is drawn: the English one asks the translator only while it is open. */}
          <Tabs
            value={side}
            onChange={(value) => {
              setSide(value === 'changes' ? 'changes' : value === 'english' ? 'english' : 'preview');
            }}
            keepMounted={false}
            className={classes.side}
          >
            <Tabs.List>
              <Tabs.Tab value="preview">Preview</Tabs.Tab>
              <Tabs.Tab value="english">English</Tabs.Tab>
              <Tabs.Tab value="changes">Changes</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="preview" pt="md">
              {preview}
            </Tabs.Panel>
            <Tabs.Panel value="english" pt="md">
              {english}
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
              setPane(value === 'preview' || value === 'english' || value === 'changes' ? value : 'write');
            }}
            data={[
              { value: 'write', label: 'Write' },
              { value: 'preview', label: 'Preview' },
              { value: 'english', label: 'English' },
              { value: 'changes', label: 'Changes' },
            ]}
          />
          <div hidden={pane !== 'write'}>{editor}</div>
          {pane === 'preview' ? preview : null}
          {pane === 'english' ? english : null}
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
                  disabled={readOnly}
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
      {replacing ? (
        <ReplaceWithFile
          recordId={data.record_id}
          onClose={() => {
            setReplacing(false);
          }}
          onRead={(content) => {
            putBack({ german: partOf(content.german) ?? german });
            setReplacing(false);
            notifyDone('Replaced with the file. Undo takes it back.');
          }}
        />
      ) : null}
      {publishing ? (
        <Publish
          text={{
            title: german.title,
            record_id: data.record_id,
            effective_from: data.effective_from,
            approved_by: data.approved_by,
            approved_on: data.approved_on,
            tells_by_default: data.tells_by_default,
            members_to_tell: data.members_to_tell,
            team_name: data.team_name,
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

/** The text replaced by uploaded files: checked as an upload is, put in as a change undo takes back. */
function ReplaceWithFile({
  recordId,
  onClose,
  onRead,
}: {
  recordId: number;
  onClose: () => void;
  onRead: (content: Schemas['ContentOut']) => void;
}) {
  const [files, setFiles] = useState<ChosenFiles>(NO_FILES);
  const [problems, setProblems] = useState<string[]>([]);
  const read = useMutation({
    mutationFn: () =>
      call(
        api.POST('/api/v1/legal/drafts/{record_id}/file', {
          params: { path: { record_id: recordId } },
          body: formOf(files),
        }),
      ),
    onSuccess: onRead,
    onError: (error) => {
      const listed = problemsOf(error);
      if (listed) setProblems(listed);
      else notifyFailed(error);
    },
  });
  return (
    <Modal opened onClose={onClose} title="Replace with a file" size="lg">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          The whole text, as a Markdown file -- downloaded from here and changed, or written elsewhere. It is
          checked as an upload is. The draft as it is now stays under History, and undo takes the change back.
        </Text>
        <Problems problems={problems} title="Not taken in, because of:" />
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
          <Button
            disabled={!files.german}
            loading={read.isPending}
            onClick={() => {
              read.mutate();
            }}
          >
            Replace
          </Button>
        </Group>
      </Stack>
    </Modal>
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
  disabled = false,
}: {
  disabled?: boolean;
  /** The view written in, asked for on a click. */
  view: () => EditorView | null;
  onUndo: () => void;
  onRedo: () => void;
}) {
  const tool = (label: string, icon: React.ReactNode, run: () => void) => (
    <Tooltip label={label} key={label}>
      <ActionIcon variant="default" size="lg" aria-label={label} onClick={run} disabled={disabled}>
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
function Preview({ recordId, part }: { recordId: number; part: Part }) {
  const [written] = useDebouncedValue(part, CHECK_AFTER_MS);
  const checked = useQuery({
    queryKey: ['legal', 'editor', recordId, 'check', written] as const,
    queryFn: () =>
      call(
        api.POST('/api/v1/legal/drafts/{record_id}/check', {
          params: { path: { record_id: recordId } },
          body: written,
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
      <h2 className={classes.previewTitle} lang="de">
        {written.title}
      </h2>
      <div
        className={textClasses.text}
        lang="de"
        // Markdown rendered on the server with HTML switched off: nothing in it can run.
        dangerouslySetInnerHTML={{ __html: checked.data.html }}
      />
    </Stack>
  );
}

/**
 * The English members will read: the German as written, translated by machine a few seconds after
 * typing stops -- paragraph by paragraph, each kept, so only what changed goes to the translator.
 */
function EnglishView({ recordId, part }: { recordId: number; part: Part }) {
  const [written] = useDebouncedValue(part, TRANSLATE_AFTER_MS);
  const translated = useQuery({
    queryKey: ['legal', 'editor', recordId, 'english', written] as const,
    queryFn: () =>
      call(
        api.POST('/api/v1/legal/drafts/{record_id}/english', {
          params: { path: { record_id: recordId } },
          body: written,
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
  if (translated.isPending) return <LoadingState />;
  if (translated.isError) {
    return <ErrorState error={translated.error} onRetry={() => void translated.refetch()} />;
  }
  if (translated.data.unavailable) {
    return (
      <Alert color="yellow" variant="light" title="No English just now">
        {translated.data.unavailable} Publishing does not wait for it: the English is made once a service
        answers.
      </Alert>
    );
  }
  return (
    <Stack gap="md">
      <Text size="sm" c="dimmed">
        Made by machine from the German, as members will read it once it is published. It is not edited by
        hand: terms are kept right by the glossary (Settings › Translation).
      </Text>
      <h2 className={classes.previewTitle} lang="en">
        {translated.data.title}
      </h2>
      <div
        className={textClasses.text}
        lang="en"
        // The translator's HTML, cleaned on the server to the tags the German page has.
        dangerouslySetInnerHTML={{ __html: translated.data.html }}
      />
    </Stack>
  );
}
