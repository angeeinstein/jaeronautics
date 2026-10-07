/**
 * An area's sidebar: the way back out, then its groups and entries. The
 * current entry is marked (aria-current); an entry with children folds them
 * open, and stays open while one of them is the current page.
 */
import { Badge, Group, NavLink, ScrollArea, Stack, Text } from '@mantine/core';
import { IconChevronLeft } from '@tabler/icons-react';
import { useState } from 'react';
import { useLocation } from 'react-router';

import { AppLink } from '../app/AppLink';
import { TeamMark } from '../components/TeamMark';
import classes from './Frame.module.css';
import { containsCurrent, isCurrent, type NavItem, type SidebarContent } from './navigation';

interface EntryProps {
  item: NavItem;
  pathname: string;
  hash: string;
  onNavigate?: () => void;
}

function Entry({ item, pathname, hash, onNavigate }: EntryProps) {
  const hasChildren = Boolean(item.children?.length);
  const current = isCurrent(item, pathname, hash) && !hasChildren;
  const [opened, setOpened] = useState(() => containsCurrent(item, pathname, hash));
  const Icon = item.icon;
  const count = item.count ?? 0;

  return (
    <NavLink
      component={AppLink}
      to={item.to}
      label={item.label}
      active={current}
      aria-current={current ? 'page' : undefined}
      className={classes.item}
      leftSection={Icon ? <Icon size={18} stroke={1.6} /> : undefined}
      rightSection={
        count > 0 ? (
          <Badge className={classes.count} size="sm" variant="filled" aria-label={`${count} waiting`}>
            {count}
          </Badge>
        ) : undefined
      }
      opened={hasChildren ? opened : undefined}
      onChange={hasChildren ? setOpened : undefined}
      onClick={hasChildren ? undefined : onNavigate}
      childrenOffset={0}
    >
      {hasChildren ? (
        <div className={classes.children}>
          {item.children?.map((child) => (
            <Entry key={child.to} item={child} pathname={pathname} hash={hash} onNavigate={onNavigate} />
          ))}
        </div>
      ) : undefined}
    </NavLink>
  );
}

export function Sidebar({ content, onNavigate }: { content: SidebarContent; onNavigate?: () => void }) {
  const { pathname, hash } = useLocation();
  return (
    <nav className={classes.sidebar} aria-label="Sections">
      <ScrollArea h="100%" type="auto">
        <Stack gap={0} p="xs">
          <AppLink to={content.back.to} className={classes.back} onClick={onNavigate}>
            <IconChevronLeft size={16} stroke={1.6} aria-hidden />
            {content.back.label}
          </AppLink>
          {content.header ? (
            <Group gap="sm" wrap="nowrap" className={classes.header}>
              <TeamMark name={content.header.label} logoUrl={content.header.logoUrl} />
              <Text fw={600} size="sm" lineClamp={2}>
                {content.header.label}
              </Text>
            </Group>
          ) : null}
          {content.groups.map((group) => (
            <section key={group.label} aria-label={group.label}>
              <div className={classes.groupLabel} aria-hidden>
                {group.label}
              </div>
              {group.items.map((item) => (
                <Entry key={item.to} item={item} pathname={pathname} hash={hash} onNavigate={onNavigate} />
              ))}
            </section>
          ))}
        </Stack>
      </ScrollArea>
    </nav>
  );
}
