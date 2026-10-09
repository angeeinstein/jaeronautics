/**
 * A team's Messages page (docs/messages-plan.md), for whoever holds
 * team.messages -- the leads:
 *
 * - **Write to the team:** to its active members, never applicants, whatever
 *   their news setting; answers come back to whoever wrote it.
 * - **Sent:** what the team sent, and how far each is.
 * - **Received:** what people wrote to the leads ("Message the leads"),
 *   answered by email and marked done.
 *
 * Data: /api/v1/teams/<slug>/manage/mailings and .../manage/messages.
 */
import { Stack, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call } from '../../../api/client';
import { type Draft, MailingComposer, MailingTable } from '../../../components/Mailing';
import { MessageList } from '../../../components/MessageList';
import { Panel } from '../../../components/Panel';
import { ErrorState, LoadingState } from '../../../components/States';
import { EVERY_30_SECONDS, useLiveRefresh } from '../../../lib/live';
import { notifyDone } from '../../../lib/notify';
import { teamQuery } from '../shared';
import { ManageHeader, useSlug } from './shared';

function Write({ slug }: { slug: string }) {
  const client = useQueryClient();
  const path = { slug };
  const key = ['teams', slug, 'mailings'] as const;
  const mailings = useQuery({
    queryKey: key,
    queryFn: () => call(api.GET('/api/v1/teams/{slug}/manage/mailings', { params: { path } })),
  });
  useLiveRefresh(
    key,
    EVERY_30_SECONDS,
    mailings.data?.items.some((item) => item.status === 'sending') ?? false,
  );
  const [draft, setDraft] = useState<Draft>({ subject: '', body: '' });
  if (mailings.isPending) return <LoadingState />;
  if (mailings.isError) return <ErrorState error={mailings.error} onRetry={() => void mailings.refetch()} />;
  return (
    <Stack gap="lg">
      <Panel title="Write to the team">
        <MailingComposer
          before={
            <Text size="sm" c="dimmed">
              To the team&apos;s active members, not applicants. Answers come back to you.
            </Text>
          }
          draft={draft}
          onDraft={setDraft}
          recipients={mailings.data.recipients}
          test={(current) =>
            call(api.POST('/api/v1/teams/{slug}/manage/mailings/test', { params: { path }, body: current }))
          }
          send={(current) =>
            call(api.POST('/api/v1/teams/{slug}/manage/mailings', { params: { path }, body: current }))
          }
          onSent={() => {
            setDraft({ subject: '', body: '' });
            notifyDone('On its way. It goes out within a few minutes.');
            void client.invalidateQueries({ queryKey: key });
          }}
        />
      </Panel>
      <Panel title="Sent" flush>
        <MailingTable items={mailings.data.items} />
      </Panel>
    </Stack>
  );
}

export function Messages() {
  const slug = useSlug();
  const client = useQueryClient();
  const path = { slug };
  return (
    <>
      <ManageHeader title="Messages" description="Write to the team, and what people wrote to its leads." />
      <Stack gap="xl">
        <Write slug={slug} />
        <Stack gap="sm">
          <Text fw={600} size="lg">
            Received
          </Text>
          <MessageList
            queryKey={['teams', slug, 'messages']}
            load={(state) =>
              call(api.GET('/api/v1/teams/{slug}/manage/messages', { params: { path, query: { state } } }))
            }
            markDone={(id, done) =>
              call(
                api.PUT('/api/v1/teams/{slug}/manage/messages/{message_id}', {
                  params: { path: { slug, message_id: id } },
                  body: { done },
                }),
              )
            }
            onChanged={() => void client.invalidateQueries({ queryKey: teamQuery(slug).queryKey })}
          />
        </Stack>
      </Stack>
    </>
  );
}
