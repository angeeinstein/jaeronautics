/**
 * A Sales page's two parts as tabs (docs/credit-plan.md, "Where it is
 * shown"): *Price list* -- with what each item sold -- and *Sales points*,
 * their menus. One thing at a time, so the page reads at a glance; the tab is
 * in the address (#sales-points), as a person's account page does it.
 */
import { Tabs } from '@mantine/core';
import { IconCpu, IconTag } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router';

export function SalesTabs({
  priceList,
  salesPoints,
}: {
  priceList: ReactNode;
  salesPoints: ReactNode | null;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  if (!salesPoints) return priceList;
  const current = location.hash === '#sales-points' ? 'sales-points' : 'price-list';
  return (
    <Tabs
      value={current}
      onChange={(tab) => {
        void navigate({ hash: tab === 'sales-points' ? 'sales-points' : '' }, { replace: true });
      }}
    >
      <Tabs.List mb="lg">
        <Tabs.Tab value="price-list" leftSection={<IconTag size={16} />}>
          Price list
        </Tabs.Tab>
        <Tabs.Tab value="sales-points" leftSection={<IconCpu size={16} />}>
          Sales points
        </Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value="price-list">{priceList}</Tabs.Panel>
      <Tabs.Panel value="sales-points">{salesPoints}</Tabs.Panel>
    </Tabs>
  );
}
