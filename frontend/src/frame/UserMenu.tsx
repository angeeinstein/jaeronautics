/**
 * The person's menu, right in the top bar: who is signed in, their account,
 * password, their data -- and logging out, which is no longer a link of its
 * own in the bar (docs/frontend-structure.md, section 3).
 */
import { Avatar, Menu, Text, UnstyledButton } from '@mantine/core';
import { IconDownload, IconKey, IconLogout, IconUser } from '@tabler/icons-react';

import { AppLink } from '../app/AppLink';
import { displayName, initials, logOut, type Me } from '../api/session';
import classes from './Frame.module.css';

export function UserMenu({ me }: { me: Me }) {
  const name = displayName(me);
  return (
    <Menu position="bottom-end" width={260} withinPortal>
      <Menu.Target>
        <UnstyledButton className={classes.userButton} aria-label={`Account menu for ${name}`}>
          <Avatar color="brand" variant="filled" size={32} radius="xl">
            {initials(me)}
          </Avatar>
          <Text size="sm" fw={500} visibleFrom="sm" truncate maw={180}>
            {name}
          </Text>
        </UnstyledButton>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>
          <Text size="sm" fw={600} c="var(--ja-text)" truncate>
            {name}
          </Text>
          <Text size="xs" truncate>
            {me.email}
          </Text>
        </Menu.Label>
        <Menu.Divider />
        <Menu.Item component={AppLink} to="/account" leftSection={<IconUser size={16} />}>
          My account
        </Menu.Item>
        <Menu.Item component={AppLink} to="/change-password" leftSection={<IconKey size={16} />}>
          Change password
        </Menu.Item>
        <Menu.Item component="a" href="/account/data-export" leftSection={<IconDownload size={16} />}>
          Download my data
        </Menu.Item>
        <Menu.Divider />
        <Menu.Item leftSection={<IconLogout size={16} />} onClick={() => void logOut()}>
          Log out
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
