/**
 * A team's settings, for its leads, each a page of its own saved on its own:
 * its page (description, longer text -- formatted, with a preview -- cover,
 * logo and photos), applying (open or not,
 * the question; the rules are the association's), the access list (who gets
 * it, when, and the email as it would go out) and its roles (leads and the
 * treasurer, chosen from a list -- by the team's leads, and by site admins,
 * who may also choose a lead from outside the team). Data: /api/v1/teams/<slug>/manage/page, /applying,
 * /access-list, /roles.
 */
import {
  ActionIcon,
  Alert,
  Anchor,
  Button,
  Checkbox,
  FileInput,
  Group,
  List,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { IconArrowDown, IconArrowUp } from '@tabler/icons-react';
import { useDebouncedValue } from '@mantine/hooks';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router';

import { api, call, type Schemas } from '../../../api/client';
import { useMe } from '../../../api/session';
import { AppLink } from '../../../app/AppLink';
import { ConfirmButton } from '../../../components/ConfirmButton';
import { type Detail, Details } from '../../../components/Details';
import { Panel } from '../../../components/Panel';
import { PdfLink } from '../../../components/PdfLink';
import { Pill } from '../../../components/Pill';
import { EmptyState, ErrorState, LoadingState } from '../../../components/States';
import { formatDate } from '../../../lib/format';
import about from '../About.module.css';
import classes from '../Teams.module.css';
import { ManageHeader, useManageChange, useSlug } from './shared';

function Loaded<T>({
  query,
  children,
}: {
  query: { isPending: boolean; isError: boolean; error: unknown; data?: T; refetch: () => unknown };
  children: (data: T) => React.ReactNode;
}) {
  if (query.isPending) return <LoadingState />;
  if (query.isError || query.data === undefined)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  return <>{children(query.data)}</>;
}

// --- The team's page ----------------------------------------------------------------------

type Page = Schemas['PageOut'];

function ImageField({
  kind,
  label,
  current,
  help,
  pageKey,
}: {
  kind: 'logo' | 'picture';
  label: string;
  current: string | null;
  help: string;
  pageKey: readonly unknown[];
}) {
  const slug = useSlug();
  const [file, setFile] = useState<File | null>(null);
  const upload = useManageChange(
    pageKey,
    async () => {
      if (!file) throw new Error('Choose a file.');
      const form = new FormData();
      form.append('image', file);
      return call(
        kind === 'logo'
          ? api.POST('/api/v1/teams/{slug}/manage/page/logo', {
              params: { path: { slug } },
              body: form as unknown as { image: string },
            })
          : api.POST('/api/v1/teams/{slug}/manage/page/picture', {
              params: { path: { slug } },
              body: form as unknown as { image: string },
            }),
      );
    },
    'Uploaded.',
  );
  const remove = useManageChange(
    pageKey,
    () =>
      call(
        kind === 'logo'
          ? api.DELETE('/api/v1/teams/{slug}/manage/page/logo', { params: { path: { slug } } })
          : api.DELETE('/api/v1/teams/{slug}/manage/page/picture', { params: { path: { slug } } }),
      ),
    'Removed.',
  );
  return (
    <Stack gap="xs">
      <Text fw={600} size="sm">
        {label}
      </Text>
      {current ? (
        <img className={kind === 'logo' ? classes.logo : classes.picture} src={current} alt="" />
      ) : (
        <Text size="sm" c="dimmed">
          {kind === 'logo' ? 'No logo' : 'No cover'}
        </Text>
      )}
      <Group align="flex-end" gap="sm">
        <FileInput
          aria-label={`${label}: a file`}
          placeholder="PNG, JPG or WebP"
          accept="image/png,image/jpeg,image/webp"
          clearable
          value={file}
          error={upload.errors.image ?? null}
          onChange={setFile}
          flex="1 1 14rem"
        />
        <Button
          variant="default"
          disabled={!file}
          loading={upload.mutation.isPending}
          onClick={() => {
            upload.mutation.mutate(undefined, {
              onSuccess: () => {
                setFile(null);
              },
            });
          }}
        >
          Upload
        </Button>
        {current ? (
          <ConfirmButton
            confirmLabel="Yes, remove"
            loading={remove.mutation.isPending}
            onConfirm={() => {
              remove.mutation.mutate(undefined);
            }}
          >
            Remove
          </ConfirmButton>
        ) : null}
      </Group>
      <Text size="xs" c="dimmed">
        {help}
      </Text>
    </Stack>
  );
}

/** The longer text as the About page will show it, from the server that formats it. */
function Preview({ text, pageKey }: { text: string; pageKey: readonly unknown[] }) {
  const slug = useSlug();
  const preview = useQuery({
    queryKey: [...pageKey, 'preview', text],
    queryFn: () =>
      call(
        api.POST('/api/v1/teams/{slug}/manage/page/preview', {
          params: { path: { slug } },
          body: { about: text || null },
        }),
      ),
  });
  if (preview.isPending) return <LoadingState />;
  if (preview.isError) return <ErrorState error={preview.error} onRetry={() => void preview.refetch()} />;
  if (!preview.data.html)
    return (
      <Text size="sm" c="dimmed">
        Nothing written yet.
      </Text>
    );
  // Formatted on the server from Markdown without HTML (services/teams.py, render_about).
  return <div className={about.story} dangerouslySetInnerHTML={{ __html: preview.data.html }} />;
}

function TextPanel({ page, pageKey }: { page: Page; pageKey: readonly unknown[] }) {
  const slug = useSlug();
  const [description, setDescription] = useState(page.description ?? '');
  const [text, setText] = useState(page.about ?? '');
  const [tab, setTab] = useState<string | null>('write');
  const save = useManageChange(
    pageKey,
    () =>
      call(
        api.PUT('/api/v1/teams/{slug}/manage/page', {
          params: { path: { slug } },
          body: { description: description || null, about: text || null },
        }),
      ),
    'Saved.',
  );
  return (
    <Panel title="Text">
      <Stack gap="md">
        <Textarea
          label="Short description"
          description="One or two sentences: on the overview of all teams, and on the cover of the About page."
          autosize
          minRows={2}
          maxLength={500}
          value={description}
          error={save.errors.description ?? null}
          onChange={(event) => {
            setDescription(event.currentTarget.value);
          }}
        />
        <Stack gap={6}>
          <Text fw={500} size="sm">
            About the team
          </Text>
          <Text size="xs" c="dimmed">
            What the team does, what members do, when it meets. Formatting: # Heading, **bold**, *italic*, -
            list, [link](https://…).
          </Text>
          <Tabs value={tab} onChange={setTab} keepMounted={false}>
            <Tabs.List>
              <Tabs.Tab value="write">Write</Tabs.Tab>
              <Tabs.Tab value="preview">Preview</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="write" pt="sm">
              <Textarea
                aria-label="About the team"
                autosize
                minRows={8}
                maxLength={10000}
                value={text}
                error={save.errors.about ?? null}
                onChange={(event) => {
                  setText(event.currentTarget.value);
                }}
              />
            </Tabs.Panel>
            <Tabs.Panel value="preview" pt="md">
              <Preview text={text} pageKey={pageKey} />
            </Tabs.Panel>
          </Tabs>
        </Stack>
        <Group justify="flex-end">
          <Button
            loading={save.mutation.isPending}
            onClick={() => {
              save.mutation.mutate(undefined);
            }}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

interface Place {
  id: number;
  caption: string;
}

/** The photos there, in their order with their captions -- changed here, then saved together. */
function PhotoList({ page, pageKey }: { page: Page; pageKey: readonly unknown[] }) {
  const slug = useSlug();
  const saved: Place[] = page.photos.map((photo) => ({ id: photo.id, caption: photo.caption ?? '' }));
  const [places, setPlaces] = useState(saved);
  const changed = JSON.stringify(places) !== JSON.stringify(saved);
  const arrange = useManageChange(
    pageKey,
    () =>
      call(
        api.PUT('/api/v1/teams/{slug}/manage/page/photos', {
          params: { path: { slug } },
          body: { photos: places.map((place) => ({ id: place.id, caption: place.caption || null })) },
        }),
      ),
    'Saved.',
  );
  const remove = useManageChange(
    pageKey,
    (id: number) =>
      call(
        api.DELETE('/api/v1/teams/{slug}/manage/page/photos/{photo_id}', {
          params: { path: { slug, photo_id: id } },
        }),
      ),
    'Removed.',
  );
  const move = (from: number, to: number) => {
    setPlaces((now) => {
      const next = [...now];
      const [place] = next.splice(from, 1);
      if (place) next.splice(to, 0, place);
      return next;
    });
  };
  if (!places.length)
    return (
      <Text size="sm" c="dimmed">
        No photos
      </Text>
    );
  const urls = new Map(page.photos.map((photo) => [photo.id, photo.url]));
  return (
    <Stack gap="sm">
      <Stack component="ol" gap="xs" m={0} p={0} aria-label="Photos" style={{ listStyle: 'none' }}>
        {places.map((place, index) => (
          <Group component="li" key={place.id} gap="sm" align="center">
            <img className={classes.thumb} src={urls.get(place.id)} alt="" />
            <TextInput
              aria-label={`Caption of photo ${String(index + 1)}`}
              placeholder="Caption (optional)"
              maxLength={200}
              value={place.caption}
              onChange={(event) => {
                const caption = event.currentTarget.value;
                setPlaces((now) => now.map((one) => (one.id === place.id ? { ...one, caption } : one)));
              }}
              flex="1 1 10rem"
              miw={0}
            />
            <Group gap={4} wrap="nowrap">
              <ActionIcon
                variant="default"
                size="lg"
                aria-label={`Move photo ${String(index + 1)} up`}
                disabled={index === 0}
                onClick={() => {
                  move(index, index - 1);
                }}
              >
                <IconArrowUp size={16} />
              </ActionIcon>
              <ActionIcon
                variant="default"
                size="lg"
                aria-label={`Move photo ${String(index + 1)} down`}
                disabled={index === places.length - 1}
                onClick={() => {
                  move(index, index + 1);
                }}
              >
                <IconArrowDown size={16} />
              </ActionIcon>
              <ConfirmButton
                confirmLabel="Yes, remove"
                loading={remove.mutation.isPending && remove.mutation.variables === place.id}
                onConfirm={() => {
                  remove.mutation.mutate(place.id);
                }}
              >
                Remove
              </ConfirmButton>
            </Group>
          </Group>
        ))}
      </Stack>
      <Group justify="flex-end">
        <Button
          disabled={!changed}
          loading={arrange.mutation.isPending}
          onClick={() => {
            arrange.mutation.mutate(undefined);
          }}
        >
          Save order and captions
        </Button>
      </Group>
    </Stack>
  );
}

function PhotosPanel({ page, pageKey }: { page: Page; pageKey: readonly unknown[] }) {
  const slug = useSlug();
  const client = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const room = page.photos_max - page.photos.length;
  const upload = useManageChange(
    pageKey,
    async () => {
      let out: Page | undefined;
      try {
        // One after the other, so they keep the order they were chosen in.
        for (const file of files) {
          const form = new FormData();
          form.append('image', file);
          out = await call(
            api.POST('/api/v1/teams/{slug}/manage/page/photos', {
              params: { path: { slug }, query: {} },
              body: form as unknown as { image: string },
            }),
          );
        }
      } catch (error) {
        // The ones before the refused one are there: show them.
        if (out) client.setQueryData(pageKey, out);
        throw error;
      }
      if (!out) throw new Error('Choose a file.');
      return out;
    },
    () => (files.length === 1 ? 'Added.' : `Added ${String(files.length)} photos.`),
  );
  const tooMany = files.length > room;
  return (
    <Panel title="Photos">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          {`Below the text on the About page, each opening large. ${String(page.photos.length)} of ${String(page.photos_max)}.`}
        </Text>
        {/* Again from the server's order after each change. */}
        <PhotoList
          key={page.photos.map((photo) => `${String(photo.id)}:${photo.caption ?? ''}`).join('|')}
          page={page}
          pageKey={pageKey}
        />
        {room > 0 ? (
          <Group align="flex-start" gap="sm">
            <FileInput
              aria-label="Photos to add"
              placeholder="PNG, JPG or WebP"
              accept="image/png,image/jpeg,image/webp"
              multiple
              clearable
              value={files}
              error={tooMany ? `Room for ${String(room)} more.` : (upload.errors.image ?? null)}
              onChange={setFiles}
              flex="1 1 14rem"
            />
            <Button
              variant="default"
              disabled={!files.length || tooMany}
              loading={upload.mutation.isPending}
              onClick={() => {
                upload.mutation.mutate(undefined, {
                  onSettled: () => {
                    setFiles([]);
                  },
                });
              }}
            >
              Add
            </Button>
          </Group>
        ) : null}
      </Stack>
    </Panel>
  );
}

function PageForm({ page, pageKey }: { page: Page; pageKey: readonly unknown[] }) {
  return (
    <Stack gap="lg">
      <TextPanel page={page} pageKey={pageKey} />
      <Panel title="Pictures">
        <SimpleGrid cols={{ base: 1, md: 2 }} spacing="xl">
          <ImageField
            kind="picture"
            label="Cover"
            current={page.picture_url}
            help="Optional. Across the top of the About page, with the team's name on its lower part: a wide picture, for example the team with its aircraft."
            pageKey={pageKey}
          />
          <ImageField
            kind="logo"
            label="Logo"
            current={page.logo_url}
            help="Optional. Shown on the portal's dark pages and on the white pages of the team's rules as a PDF, so choose one that can be seen on both."
            pageKey={pageKey}
          />
        </SimpleGrid>
      </Panel>
      <PhotosPanel page={page} pageKey={pageKey} />
    </Stack>
  );
}

export function PageSettings() {
  const slug = useSlug();
  const pageKey = ['teams', slug, 'manage', 'page'] as const;
  const page = useQuery({
    queryKey: pageKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/page', { params: { path: { slug } } })),
  });
  return (
    <>
      <ManageHeader
        title="Team page"
        description="What everybody sees about the team: on the overview and its About page."
      />
      <Loaded query={page}>{(data) => <PageForm page={data} pageKey={pageKey} />}</Loaded>
    </>
  );
}

// --- Applying -----------------------------------------------------------------------------

function ApplyingForm({
  applying,
  applyingKey,
}: {
  applying: Schemas['ApplyingOut'];
  applyingKey: readonly unknown[];
}) {
  const slug = useSlug();
  const [open, setOpen] = useState(applying.applications_open);
  const [prompt, setPrompt] = useState(applying.application_prompt ?? '');
  const save = useManageChange(
    applyingKey,
    () =>
      call(
        api.PUT('/api/v1/teams/{slug}/manage/applying', {
          params: { path: { slug } },
          body: { applications_open: open, application_prompt: prompt || null },
        }),
      ),
    'Saved.',
  );
  return (
    <Stack gap="lg">
      <Panel title="Applying">
        <Stack gap="md">
          <Checkbox
            label="Accepting new members"
            checked={open}
            onChange={(event) => {
              setOpen(event.currentTarget.checked);
            }}
          />
          <TextInput
            label="Question for applicants"
            description="Optional. Without one, applicants are not asked to write anything."
            maxLength={255}
            value={prompt}
            error={save.errors.application_prompt ?? null}
            onChange={(event) => {
              setPrompt(event.currentTarget.value);
            }}
          />
          <Group justify="flex-end">
            <Button
              loading={save.mutation.isPending}
              onClick={() => {
                save.mutation.mutate(undefined);
              }}
            >
              Save
            </Button>
          </Group>
        </Stack>
      </Panel>
      <Panel title="Rules to accept">
        <Stack gap="xs">
          {applying.rules ? (
            <Text size="sm">
              <Anchor component={AppLink} to={applying.rules.page_url} size="sm">
                Rules of the team
              </Anchor>
              {` (version of ${formatDate(applying.rules.version)}) · `}
              <PdfLink href={applying.rules.pdf_url}>PDF</PdfLink>
            </Text>
          ) : (
            <Text size="sm" c="dimmed">
              None. Whoever joins accepts the association's rules only.
            </Text>
          )}
          <Text size="xs" c="dimmed">
            The association approves and keeps every team's rules with its own legal texts, versioned, each as
            a PDF. Whoever applies or joins has to accept the version in force, and keeps the version they
            accepted. To change them, send the new text to the association.
          </Text>
        </Stack>
      </Panel>
    </Stack>
  );
}

export function Applying() {
  const slug = useSlug();
  const applyingKey = ['teams', slug, 'manage', 'applying'] as const;
  const applying = useQuery({
    queryKey: applyingKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/applying', { params: { path: { slug } } })),
  });
  return (
    <>
      <ManageHeader
        title="Applying"
        description="Whether the team takes new members, and what they are asked."
      />
      <Loaded query={applying}>{(data) => <ApplyingForm applying={data} applyingKey={applyingKey} />}</Loaded>
    </>
  );
}

// --- The access list ----------------------------------------------------------------------

type AccessList = Schemas['AccessListOut'];

function AccessListSettings({ list, listKey }: { list: AccessList; listKey: readonly unknown[] }) {
  const slug = useSlug();
  const [recipients, setRecipients] = useState(list.recipients);
  const [dates, setDates] = useState(list.dates);
  const [autoSend, setAutoSend] = useState(list.auto_send);
  const save = useManageChange(
    listKey,
    () =>
      call(
        api.PUT('/api/v1/teams/{slug}/manage/access-list', {
          params: { path: { slug } },
          body: { recipients, dates, auto_send: autoSend },
        }),
      ),
    'Saved.',
  );
  return (
    <Panel title="Who receives it, and when">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          The current members, emailed to whoever gives access to the team's rooms. The leads are copied in.
        </Text>
        <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
          <Textarea
            label="Send to"
            placeholder="One address per line"
            autosize
            minRows={2}
            value={recipients}
            error={save.errors.recipients ?? null}
            onChange={(event) => {
              setRecipients(event.currentTarget.value);
            }}
          />
          <Stack gap="xs">
            <TextInput
              label="On these days, every year"
              placeholder="15.10, 15.03"
              maxLength={255}
              value={dates}
              error={save.errors.dates ?? null}
              onChange={(event) => {
                setDates(event.currentTarget.value);
              }}
            />
            <Checkbox
              label="Send by itself on these days"
              checked={autoSend}
              onChange={(event) => {
                setAutoSend(event.currentTarget.checked);
              }}
            />
          </Stack>
        </SimpleGrid>
        <Group justify="flex-end">
          <Button
            loading={save.mutation.isPending}
            onClick={() => {
              save.mutation.mutate(undefined);
            }}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function AccessListPreview({ list, listKey }: { list: AccessList; listKey: readonly unknown[] }) {
  const slug = useSlug();
  const send = useManageChange(
    listKey,
    () => call(api.POST('/api/v1/teams/{slug}/manage/access-list/send', { params: { path: { slug } } })),
    'Sent.',
  );
  const missing = list.rows.filter((row) => !row.email).length;
  const status: Detail[] = [
    [
      'By itself',
      list.auto_send && list.next_on ? `On ${list.dates}; next on ${formatDate(list.next_on)}` : 'Off',
    ],
    ['Last sent', list.last_sent_on ? formatDate(list.last_sent_on) : 'Never'],
  ];
  return (
    <Panel
      title="The email"
      actions={
        list.to.length ? (
          <ConfirmButton
            color="brand"
            confirmLabel="Yes, send"
            loading={send.mutation.isPending}
            onConfirm={() => {
              send.mutation.mutate(undefined);
            }}
          >
            Send now
          </ConfirmButton>
        ) : null
      }
    >
      <Stack gap="md">
        <Details items={status} />
        {!list.to.length ? (
          <Alert color="amber" variant="light">
            Add who receives it first.
          </Alert>
        ) : null}
        {missing ? (
          <Alert
            color="amber"
            variant="light"
          >{`${String(missing)} member(s) have no university email.`}</Alert>
        ) : null}
        <Details
          items={[
            ['To', list.to.join(', ')],
            ['Copy', list.cc.join(', ')],
            ['Subject', list.subject],
          ]}
        />
        <Text size="sm">{list.intro}</Text>
        {list.rows.length ? (
          <Table.ScrollContainer minWidth={480} type="native">
            <Table verticalSpacing="xs" aria-label="On the list">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Name</Table.Th>
                  <Table.Th scope="col">University email</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {list.rows.map((row) => (
                  <Table.Tr key={`${row.name}-${row.email ?? ''}`}>
                    <Table.Td>
                      {row.name} {row.new ? <Pill tone="info">New</Pill> : null}
                    </Table.Td>
                    <Table.Td>{row.email ?? '–'}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <EmptyState>The team has no members at the moment.</EmptyState>
        )}
        <Text size="xs" c="dimmed">
          {`${String(list.rows.length)} member(s).`}
          {list.compared_note ? ` ${list.compared_note}` : ''}
        </Text>
        {list.gone.length ? (
          <Stack gap={4}>
            <Text size="sm" fw={600} c="dimmed">
              No longer in the team
            </Text>
            <List size="sm" c="dimmed">
              {list.gone.map((entry) => (
                <List.Item
                  key={`${entry.name ?? ''}-${entry.email ?? ''}`}
                >{`${entry.name ?? '–'} · ${entry.email ?? '–'}`}</List.Item>
              ))}
            </List>
          </Stack>
        ) : null}
      </Stack>
    </Panel>
  );
}

export function AccessListPage() {
  const slug = useSlug();
  const listKey = ['teams', slug, 'manage', 'access-list'] as const;
  const list = useQuery({
    queryKey: listKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/access-list', { params: { path: { slug } } })),
  });
  return (
    <>
      <ManageHeader title="Access list" description="The current members, for access to the team's rooms." />
      <Loaded query={list}>
        {(data) => (
          <Stack gap="lg">
            {data.may_edit ? (
              <AccessListSettings
                key={`${data.recipients}|${data.dates}|${String(data.auto_send)}`}
                list={data}
                listKey={listKey}
              />
            ) : null}
            <AccessListPreview list={data} listKey={listKey} />
          </Stack>
        )}
      </Loaded>
    </>
  );
}

// --- Roles --------------------------------------------------------------------------------

type Roles = Schemas['TeamRolesOut'];

function Holder({ holder }: { holder: Schemas['HolderOut'] }) {
  return (
    <>
      {holder.name}
      {holder.in_force ? null : (
        <Text span size="xs" c="dimmed">
          {' '}
          (not in force)
        </Text>
      )}
    </>
  );
}

/** Choosing a new lead: the team's members -- and, for a site admin who types a name, the association's. */
function LeadPicker({ roles, rolesKey }: { roles: Roles; rolesKey: readonly unknown[] }) {
  const slug = useSlug();
  const [chosen, setChosen] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [query] = useDebouncedValue(search.trim(), 250);
  const found = useQuery({
    queryKey: [...rolesKey, 'lead-candidates', query],
    queryFn: () =>
      call(
        api.GET('/api/v1/teams/{slug}/manage/lead-candidates', {
          params: { path: { slug }, query: { q: query } },
        }),
      ),
    enabled: roles.searches_everyone && query.length >= 2,
    placeholderData: keepPreviousData,
  });
  const appoint = useManageChange(
    rolesKey,
    () =>
      call(
        api.POST('/api/v1/teams/{slug}/manage/leads', {
          params: { path: { slug } },
          body: { user_id: Number(chosen) },
        }),
      ),
    'Lead appointed.',
  );

  const label = (candidate: Schemas['CandidateOut']) =>
    candidate.in_team || !candidate.detail ? candidate.name : `${candidate.name} · ${candidate.detail}`;
  const inTeam = roles.lead_candidates.map((candidate) => ({
    value: String(candidate.user_id),
    label: label(candidate),
  }));
  const known = new Set(inTeam.map((option) => option.value));
  const others = (found.data?.items ?? [])
    .filter((candidate) => !candidate.in_team && !known.has(String(candidate.user_id)))
    .map((candidate) => ({ value: String(candidate.user_id), label: label(candidate) }));
  const fromServer = new Set(others.map((option) => option.value));
  const data = roles.searches_everyone
    ? [
        { group: 'In the team', items: inTeam },
        { group: 'Other members of the association', items: others },
      ].filter((group) => group.items.length)
    : inTeam;
  if (!inTeam.length && !roles.searches_everyone) return null;

  return (
    <Group align="flex-end" gap="sm">
      <Select
        label="Appoint a lead"
        placeholder={roles.searches_everyone ? 'Choose, or type a name…' : 'Choose…'}
        description={
          roles.searches_everyone
            ? 'Somebody not in the team yet becomes a lead once they have joined it.'
            : undefined
        }
        data={data}
        value={chosen}
        searchable
        searchValue={search}
        onSearchChange={setSearch}
        // The server has already searched the association; the team's own are filtered here.
        filter={({ options, search: typed }) =>
          options.filter((option) =>
            'group' in option
              ? true
              : fromServer.has(option.value) ||
                option.label.toLowerCase().includes(typed.toLowerCase().trim()),
          )
        }
        nothingFoundMessage={roles.searches_everyone && query.length < 2 ? 'Type a name' : 'Nobody found'}
        error={appoint.errors.user_id ?? null}
        onChange={setChosen}
        flex="1 1 18rem"
      />
      <Button
        variant="default"
        disabled={!chosen}
        loading={appoint.mutation.isPending}
        onClick={() => {
          appoint.mutation.mutate(undefined, {
            onSuccess: () => {
              setChosen(null);
              setSearch('');
            },
          });
        }}
      >
        Appoint
      </Button>
    </Group>
  );
}

function LeadsPanel({ roles, rolesKey }: { roles: Roles; rolesKey: readonly unknown[] }) {
  const slug = useSlug();
  const me = useMe().data;
  const go = useNavigate();
  const dismiss = useManageChange(
    rolesKey,
    ({ userId, confirmed }: { userId: number; confirmed: boolean }) =>
      call(
        api.DELETE('/api/v1/teams/{slug}/manage/leads/{user_id}', {
          params: { path: { slug, user_id: userId }, query: { confirmed } },
        }),
      ),
    'No longer a lead.',
  );
  const last = roles.leads.length <= 1;
  return (
    <Panel title="Leads">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Run the team: its members, applications, money and settings. Appointed by the team's leads and the
          association's admins.
        </Text>
        {roles.leads.length ? (
          <Stack gap="xs">
            {roles.leads.map((holder) => {
              const self = holder.user_id === me?.id;
              return (
                <Group key={holder.user_id} gap="sm">
                  <Text size="sm">
                    <Holder holder={holder} />
                  </Text>
                  {roles.may_appoint_leads ? (
                    <ConfirmButton
                      size="xs"
                      confirmLabel={
                        last ? 'Yes, remove the last lead' : self ? 'Yes, step down' : 'Yes, remove'
                      }
                      loading={
                        dismiss.mutation.isPending && dismiss.mutation.variables.userId === holder.user_id
                      }
                      onConfirm={() => {
                        dismiss.mutation.mutate(
                          { userId: holder.user_id, confirmed: last },
                          {
                            onSuccess: () => {
                              // Without the role, the team's management is not theirs any more.
                              if (self && !me.permissions.includes('teams.manage')) void go(`/teams/${slug}`);
                            },
                          },
                        );
                      }}
                    >
                      {self ? 'Step down' : 'Remove'}
                    </ConfirmButton>
                  ) : null}
                </Group>
              );
            })}
          </Stack>
        ) : (
          <EmptyState>None.</EmptyState>
        )}
        {roles.may_appoint_leads ? <LeadPicker roles={roles} rolesKey={rolesKey} /> : null}
      </Stack>
    </Panel>
  );
}

function RolesBody({ roles, rolesKey }: { roles: Roles; rolesKey: readonly unknown[] }) {
  const slug = useSlug();
  const [chosen, setChosen] = useState<string | null>(null);
  const appoint = useManageChange(
    rolesKey,
    () =>
      call(
        api.POST('/api/v1/teams/{slug}/manage/treasurer', {
          params: { path: { slug } },
          body: { user_id: Number(chosen) },
        }),
      ),
    'Treasurer appointed.',
  );
  const dismiss = useManageChange(
    rolesKey,
    (userId: number) =>
      call(
        api.DELETE('/api/v1/teams/{slug}/manage/treasurer/{user_id}', {
          params: { path: { slug, user_id: userId } },
        }),
      ),
    'No longer treasurer.',
  );
  return (
    <Stack gap="lg">
      <LeadsPanel roles={roles} rolesKey={rolesKey} />
      <Panel title="Treasurer">
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            Sees what the team's members paid and what was transferred to the team, and keeps its bank
            details. Nothing about the people.
          </Text>
          {roles.treasurers.length ? (
            <Stack gap="xs">
              {roles.treasurers.map((holder) => (
                <Group key={holder.user_id} gap="sm">
                  <Text size="sm">
                    <Holder holder={holder} />
                  </Text>
                  {roles.may_appoint ? (
                    <ConfirmButton
                      size="xs"
                      confirmLabel="Yes, remove"
                      loading={dismiss.mutation.isPending && dismiss.mutation.variables === holder.user_id}
                      onConfirm={() => {
                        dismiss.mutation.mutate(holder.user_id);
                      }}
                    >
                      Remove
                    </ConfirmButton>
                  ) : null}
                </Group>
              ))}
            </Stack>
          ) : (
            <EmptyState>None.</EmptyState>
          )}
          {roles.may_appoint && roles.candidates.length ? (
            <Group align="flex-end" gap="sm">
              <Select
                label="Appoint a treasurer"
                placeholder="Choose…"
                data={roles.candidates.map((candidate) => ({
                  value: String(candidate.user_id),
                  label: candidate.name,
                }))}
                value={chosen}
                error={appoint.errors.user_id ?? null}
                onChange={setChosen}
                searchable
                flex="1 1 16rem"
              />
              <Button
                variant="default"
                disabled={!chosen}
                loading={appoint.mutation.isPending}
                onClick={() => {
                  appoint.mutation.mutate(undefined, {
                    onSuccess: () => {
                      setChosen(null);
                    },
                  });
                }}
              >
                Appoint
              </Button>
            </Group>
          ) : null}
        </Stack>
      </Panel>
    </Stack>
  );
}

export function RolesPage() {
  const slug = useSlug();
  const rolesKey = ['teams', slug, 'manage', 'roles'] as const;
  const roles = useQuery({
    queryKey: rolesKey,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/roles', { params: { path: { slug } } })),
  });
  return (
    <>
      <ManageHeader title="Roles" description="Who leads the team, and who keeps its money." />
      <Loaded query={roles}>{(data) => <RolesBody roles={data} rolesKey={rolesKey} />}</Loaded>
    </>
  );
}
