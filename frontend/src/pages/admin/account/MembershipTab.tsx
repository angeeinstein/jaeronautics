/**
 * The membership: who the member is on record, their state and paid period,
 * and -- where Stripe holds something for them -- asking Stripe again, for a
 * webhook that never arrived.
 */
import { Button, Group, Stack, Text } from '@mantine/core';

import { api, call } from '../../../api/client';
import { Details } from '../../../components/Details';
import { Panel } from '../../../components/Panel';
import { Pill } from '../../../components/Pill';
import { EmptyState } from '../../../components/States';
import { formatDate, formatDateTime } from '../../../lib/format';
import { notifyDone, notifyNote } from '../../../lib/notify';
import { MEMBERSHIP_PILL } from '../accountLabels';
import { type Account, path, useAccountAction } from './shared';

function SyncBilling({ account }: { account: Account }) {
  const sync = useAccountAction(
    () => call(api.POST('/api/v1/admin/accounts/{user_id}/billing-sync', path(account.id))),
    {
      done: (answer) => {
        if (answer.changed) notifyDone('Updated from Stripe.');
        else notifyNote('Already the same as at Stripe.');
        if (answer.forum_error) notifyNote(`The forum could not be updated: ${answer.forum_error}`);
      },
    },
  );
  return (
    <Group gap="sm" mt="md">
      <Button
        variant="default"
        loading={sync.isPending}
        onClick={() => {
          sync.mutate(undefined);
        }}
      >
        Sync with Stripe
      </Button>
      <Text size="sm" c="dimmed">
        Asks Stripe directly and repairs what a missed update left behind.
      </Text>
    </Group>
  );
}

export function MembershipTab({ account }: { account: Account }) {
  const membership = account.membership;
  if (!membership) {
    return (
      <Panel title="Membership">
        <EmptyState>
          {account.old_forum
            ? 'Not recorded: joined before the portal existed.'
            : 'This account has no membership.'}
        </EmptyState>
      </Panel>
    );
  }
  const pill = MEMBERSHIP_PILL[membership.state];
  return (
    <Stack gap="lg">
      <Panel title="Membership" actions={pill ? <Pill tone={pill.tone}>{pill.label}</Pill> : null}>
        <Details
          items={[
            [
              'Name',
              [membership.title, membership.first_name, membership.last_name].filter(Boolean).join(' '),
            ],
            ['Private address', membership.email_private],
            ['Kind', membership.category_label],
            ['Year group', membership.year_group],
            ['Company', membership.company_name],
            ['Payment', membership.payment_status_label],
            [
              'Paid until',
              membership.ends_on ? (
                <span className="ja-figures">{formatDate(membership.ends_on)}</span>
              ) : null,
            ],
            [
              'Renews',
              membership.renews_on ? (
                <span className="ja-figures">{formatDate(membership.renews_on)}</span>
              ) : (
                'No, it ends with the paid period'
              ),
            ],
            ...(membership.picture_replacement_allowed_since
              ? ([
                  ['New picture allowed since', formatDateTime(membership.picture_replacement_allowed_since)],
                ] as [string, string][])
              : []),
          ]}
        />
        {account.actions.sync_billing ? <SyncBilling account={account} /> : null}
      </Panel>
    </Stack>
  );
}
