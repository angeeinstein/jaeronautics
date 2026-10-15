/**
 * A Sales page's two parts as tabs (docs/credit-plan.md, "Where it is
 * shown"): *Price list* -- with what each item sold -- and *Sales points*,
 * their menus (#sales-points). Only the price list while there are no sales
 * points.
 */
import { IconCpu, IconTag } from '@tabler/icons-react';
import type { ReactNode } from 'react';

import { PageTabs } from './PageTabs';

export function SalesTabs({
  priceList,
  salesPoints,
}: {
  priceList: ReactNode;
  salesPoints: ReactNode | null;
}) {
  return (
    <PageTabs
      tabs={[
        { value: 'price-list', label: 'Price list', icon: IconTag, content: priceList },
        { value: 'sales-points', label: 'Sales points', icon: IconCpu, content: salesPoints },
      ]}
    />
  );
}
