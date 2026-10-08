/**
 * A new team: its details and, if it charges, its fee. Its leads are given
 * once it exists, on its page.
 */
import { Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call } from '../../../api/client';
import { useGo } from '../../../app/useGo';
import { PageHeader } from '../../../components/PageHeader';
import { Panel } from '../../../components/Panel';
import { notifyDone } from '../../../lib/notify';
import { teamsQuery, useTeamChange } from './shared';
import { type Details, DetailsFields, type FeeFields, FeeFieldsEditor } from './TeamFields';

export function NewTeam() {
  const go = useGo();
  const teams = useQuery(teamsQuery);
  const singular = teams.data?.settings.label_singular ?? 'Team';
  const plural = teams.data?.settings.label_plural ?? 'Teams';
  const [details, setDetails] = useState<Details>({
    name: '',
    admission_mode: 'approval',
    max_members: null,
    forum_group: null,
    access_list_enabled: false,
  });
  const [slug, setSlug] = useState('');
  const [fee, setFee] = useState<FeeFields>({
    payment_mode: 'none',
    stripe_price_id: null,
    period_starts: null,
  });
  const create = useTeamChange(
    () =>
      call(
        api.POST('/api/v1/admin/teams', {
          body: { ...details, slug: slug.trim() || null, fee: fee.payment_mode === 'none' ? null : fee },
        }),
      ),
    {
      done: (team) => {
        notifyDone('Created. Give it a lead next.');
        // Straight into the team's own pages, where the rest is set up.
        go(`/teams/${team.slug}/manage/details`);
      },
    },
  );

  return (
    <>
      <PageHeader
        title={`New ${singular}`}
        description="Give it a lead once it exists."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: plural, to: '/admin/teams' }, { label: 'New' }]}
      />
      <Stack gap="lg">
        <Panel title="Details">
          <Stack gap="md">
            <DetailsFields value={details} onChange={setDetails} />
            <TextInput
              label="Short name"
              description="Used in links: lowercase letters, digits and hyphens. Made from the name if left empty. Cannot be changed later."
              maxLength={60}
              value={slug}
              onChange={(event) => {
                setSlug(event.currentTarget.value);
              }}
              maw={360}
            />
            <Text size="sm" c="dimmed">
              Descriptions, pictures, logo, the question for applicants and rules: the leads keep those on the
              team&apos;s management page, which admins can open too.
            </Text>
          </Stack>
        </Panel>
        <Panel title="Fee">
          <FeeFieldsEditor value={fee} onChange={setFee} />
        </Panel>
        <Group justify="flex-end">
          <Button
            loading={create.isPending}
            disabled={!details.name.trim()}
            onClick={() => {
              create.mutate(undefined);
            }}
          >
            Create
          </Button>
        </Group>
      </Stack>
    </>
  );
}
