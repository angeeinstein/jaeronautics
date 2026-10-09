/**
 * Admin › Messages: what came through the contact form (docs/messages-plan.md),
 * for everybody who receives it (messages.receive). Answered by email, then
 * marked done. Data: GET /api/v1/admin/messages, PUT .../<id>.
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
        description="What people wrote through the contact form. Answer by email, then mark it done."
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
      />
      <Text size="sm" c="dimmed" mt="lg">
        Every admin receives these by email. Messages done are deleted after a year.
      </Text>
    </>
  );
}
