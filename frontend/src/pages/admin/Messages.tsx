/**
 * Admin › Messages: what came through the contact form (docs/messages-plan.md),
 * for everybody who receives it (messages.receive). Answered here -- sent from
 * the mail account chosen for replies in Settings › Email, not one's own
 * mailbox -- and then done. Data: GET /api/v1/admin/messages, PUT .../<id>,
 * POST .../<id>/replies.
 */
import { Text } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';

import { api, call } from '../../api/client';
import { meQuery } from '../../api/session';
import { MessageList } from '../../components/MessageList';
import { PageHeader } from '../../components/PageHeader';

export function Messages() {
  const client = useQueryClient();
  return (
    <>
      <PageHeader
        title="Messages"
        description="What people wrote through the contact form. Reply here; it is then done."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Messages' }]}
      />
      <MessageList
        queryKey={['admin', 'messages']}
        accountLinks
        load={(state) => call(api.GET('/api/v1/admin/messages', { params: { query: { state } } }))}
        markDone={(id, done) =>
          call(
            api.PUT('/api/v1/admin/messages/{message_id}', {
              params: { path: { message_id: id } },
              body: { done },
            }),
          )
        }
        onChanged={() => void client.invalidateQueries({ queryKey: meQuery.queryKey })}
        reply={(id, body) =>
          call(
            api.POST('/api/v1/admin/messages/{message_id}/replies', {
              params: { path: { message_id: id } },
              body: { body },
            }),
          )
        }
      />
      <Text size="sm" c="dimmed" mt="lg">
        Every admin receives these by email. Answers to a reply come back here when the replies&apos; account
        is a Microsoft 365 mailbox; otherwise they arrive in its mailbox (Settings › Email). Messages done are
        deleted after a year, with their answers.
      </Text>
    </>
  );
}
