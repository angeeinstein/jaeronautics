/**
 * My Account (docs/frontend-structure.md, 6): what is to be done first --
 * addresses waiting to be confirmed -- then the membership and the forum,
 * the email addresses and one's teams, side by side on a wide screen; then
 * the contact details, the name and kind of membership, and one's data.
 * An account without a membership -- one used only to run the portal --
 * has the parts there are, and the way to start one.
 *
 * Data: GET /api/v1/account (aeronautics_members/api/account.py), asked again
 * every few seconds while a payment just made is being confirmed.
 */
import { Button, Group, SimpleGrid, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { AppLink } from '../../app/AppLink';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { ErrorState, LoadingState } from '../../components/States';
import { useMe, can } from '../../api/session';
import { ContactCard } from './Contact';
import { EmailsCard } from './Emails';
import { ForumCard } from './ForumCard';
import { IdentityCard } from './Identity';
import { MembershipCard } from './Membership';
import { type Account as AccountData, accountQuery, Note, useFormOptions } from './shared';
import { TeamsCard } from './TeamsCard';
import { YourDataCard } from './YourData';

function NoMembership() {
  const me = useMe();
  return (
    <Panel title="Membership">
      <Stack gap="sm">
        <Text>This account has no membership.</Text>
        <Text size="sm" c="dimmed">
          {can(me.data, 'admin.access')
            ? 'An account used only to run the portal does not need one. If you are also a member of the association, you can start your membership with this login.'
            : 'You can start your membership with this login.'}
        </Text>
        <Group>
          <Button component={AppLink} to="/account/create-membership">
            Become a member
          </Button>
        </Group>
      </Stack>
    </Panel>
  );
}

function Body({ account }: { account: AccountData }) {
  const options = useFormOptions();
  const member = account.member;
  const teams = member?.teams;
  return (
    <Stack gap="lg">
      {account.to_confirm ? <Note note={account.to_confirm} /> : null}
      {member ? (
        <>
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
            <MembershipCard membership={member.membership} />
            <ForumCard forum={member.forum} />
            <EmailsCard email={account.email} workEmail={member.work_email} />
            {teams && (teams.mine.length || teams.invite) ? <TeamsCard teams={teams} /> : null}
          </SimpleGrid>
          {options.data ? (
            <>
              <ContactCard
                key={JSON.stringify(member.contact)}
                contact={member.contact}
                options={options.data}
              />
              <IdentityCard
                key={member.change_request?.id ?? 'form'}
                identity={member.identity}
                request={member.change_request}
                options={options.data}
              />
            </>
          ) : options.isError ? (
            <ErrorState error={options.error} onRetry={() => void options.refetch()} />
          ) : (
            <LoadingState />
          )}
        </>
      ) : (
        <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
          <NoMembership />
          <EmailsCard email={account.email} workEmail={null} />
        </SimpleGrid>
      )}
      <YourDataCard
        exportUrl={account.export_url}
        deletionNote={member?.deletion_note ?? null}
        mayCancelInstead={member?.may_cancel_instead ?? false}
      />
    </Stack>
  );
}

export function Account() {
  const account = useQuery({
    ...accountQuery,
    // Stripe's confirmation of a payment just made arrives within seconds.
    refetchInterval: (query) => (query.state.data?.member?.membership.activating ? 3000 : false),
  });
  return (
    <>
      <PageHeader
        title="My Account"
        description={account.data?.email.address}
        actions={
          <Button component={AppLink} to="/change-password" variant="default">
            Change password
          </Button>
        }
      />
      {account.isPending ? (
        <LoadingState />
      ) : account.isError ? (
        <ErrorState error={account.error} onRetry={() => void account.refetch()} />
      ) : (
        <Body account={account.data} />
      )}
    </>
  );
}
