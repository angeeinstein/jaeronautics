/**
 * The icon of an item on a price list, as the portal shows it
 * (docs/device-protocol.md, "Icons"). The names come from the server
 * (credit_sales.ICONS); the devices carry pictures of their own for them.
 * A name not known here is shown as "other".
 */
import {
  IconBeer,
  IconBolt,
  IconBottle,
  IconCandy,
  IconCoffee,
  IconCup,
  IconDroplet,
  IconIceCream2,
  IconLeaf,
  IconLemon,
  IconMug,
  IconPaperBag,
  IconTag,
  type IconProps,
} from '@tabler/icons-react';
import type { ComponentType } from 'react';

const ICONS: Record<string, ComponentType<IconProps>> = {
  coffee: IconCoffee,
  tea: IconLeaf,
  hot_chocolate: IconMug,
  beer: IconBeer,
  soft_drink: IconCup,
  mate: IconBottle,
  energy_drink: IconBolt,
  juice: IconLemon,
  water: IconDroplet,
  snack: IconPaperBag,
  sweets: IconCandy,
  ice_cream: IconIceCream2,
  other: IconTag,
};

export function ItemIcon({ icon, size = 18 }: { icon: string; size?: number }) {
  const Shown = ICONS[icon] ?? IconTag;
  return <Shown size={size} stroke={1.6} aria-hidden />;
}
