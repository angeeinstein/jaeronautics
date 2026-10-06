/**
 * One account (docs/frontend-structure.md, 4): the name with its state, the
 * address under it, then a tab per subject. The open tab is in the address
 * (#membership), so a link or a reload lands on it. Tabs only for what the
 * person looking may see; actions only where they would not be refused --
 * the server says which (api/admin_account.py).
 */
import { Group, Tabs, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate, useParams } from 'react-router';

import { useMe } from '../../../api/session';
import { PageHeader } from '../../../components/PageHeader';
import { Pill } from '../../../components/Pill';
import { ErrorState, LoadingState } from '../../../components/States';
import { ACCOUNT_STATE_PILL, MEMBERSHIP_PILL } from '../accountLabels';
import { ActivityTab } from './ActivityTab';
import { DangerZoneTab } from './DangerZoneTab';
import { ForumTab } from './ForumTab';
import { MembershipTab } from './MembershipTab';
import { ProfileTab } from './ProfileTab';
import { RolesTab } from './RolesTab';
import { type Account as AccountData, accountQuery } from './shared';
import { TeamsTab } from './TeamsTab';

const TABS = ['profile', 'membership', 'forum', 'teams', 'roles', 'danger', 'activity'] as const;
type Tab = (typeof TABS)[number];

/** The one state worth saying beside the name: what stops the account, else the membership. */
function Status({ account }: { account: AccountData }) {
  const pill =
    ACCOUNT_STATE_PILL[account.account_state] ??
    (account.membership ? MEMBERSHIP_PILL[account.membership.state] : null) ??
    (account.old_forum?.claimed_at === null ? { label: 'Old forum', tone: 'info' as const } : null);
  return pill ? <Pill tone={pill.tone}>{pill.label}</Pill> : null;
}

function shownTabs(account: AccountData): Tab[] {
  return TABS.filter((tab) => {
    if (tab === 'teams') return account.teams !== null;
    if (tab === 'danger') return account.actions.manage_access || account.actions.erase;
    return true;
  });
}

export function Account() {
  const { userId } = useParams();
  const id = Number(userId);
  const account = useQuery({ ...accountQuery(id), enabled: Number.isInteger(id) });
  const me = useMe();
  const { hash } = useLocation();
  const navigate = useNavigate();

  const title = account.data ? (account.data.name ?? account.data.email) : 'Account';
  const crumbs = [
    { label: 'Admin', to: '/admin' },
    { label: 'Accounts', to: '/admin/accounts' },
    { label: title },
  ];

  if (account.isPending || !account.data) {
    return (
      <>
        <PageHeader title={title} crumbs={crumbs} />
        {account.isError ? (
          <ErrorState error={account.error} onRetry={() => void account.refetch()} />
        ) : (
          <LoadingState />
        )}
      </>
    );
  }

  const data = account.data;
  const tabs = shownTabs(data);
  const wanted = hash.slice(1) as Tab;
  const current: Tab = tabs.includes(wanted) ? wanted : 'profile';
  const teamsTitle = me.data?.team_labels.plural ?? 'Teams';
  const labels: Record<Tab, string> = {
    profile: 'Profile',
    membership: 'Membership',
    forum: 'Forum',
    teams: teamsTitle,
    roles: 'Roles',
    danger: 'Danger zone',
    activity: 'Activity',
  };

  return (
    <>
      <PageHeader
        title={title}
        crumbs={crumbs}
        description={
          <Group gap="sm" component="span">
            <Status account={data} />
            <Text span c="dimmed">
              {data.email}
            </Text>
          </Group>
        }
      />
      <Tabs
        value={current}
        onChange={(tab) => {
          void navigate({ hash: tab === 'profile' || !tab ? '' : tab }, { replace: true });
        }}
        keepMounted={false}
      >
        <Tabs.List mb="lg">
          {tabs.map((tab) => (
            <Tabs.Tab key={tab} value={tab} color={tab === 'danger' ? 'red' : undefined}>
              {labels[tab]}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        <Tabs.Panel value="profile">
          <ProfileTab account={data} />
        </Tabs.Panel>
        <Tabs.Panel value="membership">
          <MembershipTab account={data} />
        </Tabs.Panel>
        <Tabs.Panel value="forum">
          <ForumTab account={data} />
        </Tabs.Panel>
        {data.teams ? (
          <Tabs.Panel value="teams">
            <TeamsTab teams={data.teams} title={teamsTitle} />
          </Tabs.Panel>
        ) : null}
        <Tabs.Panel value="roles">
          <RolesTab account={data} />
        </Tabs.Panel>
        <Tabs.Panel value="danger">
          <DangerZoneTab account={data} />
        </Tabs.Panel>
        <Tabs.Panel value="activity">
          <ActivityTab account={data} />
        </Tabs.Panel>
      </Tabs>
    </>
  );
}
