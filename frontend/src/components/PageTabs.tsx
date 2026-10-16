/**
 * A long page's parts as tabs (docs/frontend-structure.md, "Page patterns"):
 * one thing at a time, so the page reads at a glance -- the Sales pages, a
 * device, a team's money, Updates, Backup. The tab is in the address (#…),
 * so a link and the back button keep it; the first tab has none. A tab with
 * nothing in it is left out, and a single one is shown without the tab row.
 */
import { Tabs } from '@mantine/core';
import type { IconProps } from '@tabler/icons-react';
import type { ComponentType, ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router';

export interface PageTab {
  /** Its name in the address: "#sales-points". */
  value: string;
  label: string;
  icon: ComponentType<IconProps>;
  /** What it shows; null: the tab is left out. */
  content: ReactNode;
}

export function PageTabs({ tabs }: { tabs: PageTab[] }) {
  const location = useLocation();
  const navigate = useNavigate();
  const shown = tabs.filter((tab) => tab.content !== null && tab.content !== undefined);
  const [first] = shown;
  if (!first) return null;
  if (shown.length === 1) return <>{first.content}</>;
  const asked = location.hash.replace(/^#/, '');
  const current = shown.some((tab) => tab.value === asked) ? asked : first.value;
  return (
    <Tabs
      value={current}
      onChange={(tab) => {
        void navigate({ hash: tab && tab !== first.value ? tab : '' }, { replace: true });
      }}
    >
      <Tabs.List mb="lg">
        {shown.map(({ value, label, icon: Icon }) => (
          <Tabs.Tab key={value} value={value} leftSection={<Icon size={16} />}>
            {label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      {shown.map((tab) => (
        <Tabs.Panel key={tab.value} value={tab.value}>
          {tab.content}
        </Tabs.Panel>
      ))}
    </Tabs>
  );
}
