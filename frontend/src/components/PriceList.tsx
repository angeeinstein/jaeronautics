/**
 * A seller's price list for credit (docs/credit-plan.md, "Selling"): the
 * association's (Admin › Sales) or a team's (its Sales page). Each item has a
 * name (at most 20 characters: four share a sales point's small screen), a
 * price and an icon -- the picture beside it on the screen, chosen from a
 * fixed list; it can be renamed, repriced, given another icon, moved,
 * switched off or deleted. With each item, how many were sold and what they
 * made, the last 30 days or ever; below, what was sold of items deleted
 * since, folded into one row that opens to show them -- they would pile up
 * over the years -- and still counted in *Together*. In *30 days* the row
 * counts only those sold or taken back then, and is not there when there are
 * none. A new
 * price counts for the next sale; sales made keep theirs.
 */
import {
  ActionIcon,
  Button,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  type SelectProps,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Tooltip,
  UnstyledButton,
  VisuallyHidden,
} from '@mantine/core';
import {
  IconArrowDown,
  IconArrowUp,
  IconChevronRight,
  IconPencil,
  IconPlus,
  IconTag,
  IconTrash,
} from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError, type Schemas } from '../api/client';
import { formatEuros } from '../lib/format';
import { notifyFailed } from '../lib/notify';
import { ItemIcon } from './ItemIcon';
import { Panel } from './Panel';
import { EmptyState } from './States';

export type PriceList = Schemas['PriceListOut'];
type Item = Schemas['PriceItemOut'];
type Icon = Schemas['ItemIconOut'];
export type ItemChange = Schemas['PriceItemChangeIn'];
export type NewItem = Schemas['PriceItemIn'];
type Sales = Schemas['SellerSalesOut'];
type ItemSales = Sales['items'][number];
type Period = '30' | 'ever';

/** An item's name: four share a sales point's small screen (credit_sales.NAME_LENGTH). */
const NAME_LENGTH = 20;

interface PriceListProps {
  title: string;
  list: PriceList;
  queryKey: readonly unknown[];
  add: (item: NewItem) => Promise<PriceList>;
  change: (id: number, change: ItemChange) => Promise<PriceList>;
  remove: (id: number) => Promise<PriceList>;
  /** What each item sold: shown in the list when given. */
  sales?: Sales;
  /** Refreshed after a change, since what is listed changes with it. */
  salesKey?: readonly unknown[];
}

function figures(found: ItemSales | undefined, period: Period): { sold: number; made: number } {
  if (!found) return { sold: 0, made: 0 };
  return period === '30'
    ? { sold: found.sold_30_days, made: found.earned_30_days }
    : { sold: found.sold, made: found.earned };
}

function NameCount({ name }: { name: string }) {
  return `${String(name.length)}/${String(NAME_LENGTH)}`;
}

/** Each icon with its picture, in the list and in the field. */
const renderIcon: SelectProps['renderOption'] = ({ option }) => (
  <Group gap="xs" wrap="nowrap">
    <ItemIcon icon={option.value} />
    <span>{option.label}</span>
  </Group>
);

function IconField({
  icons,
  value,
  error,
  onChange,
}: {
  icons: Icon[];
  value: string | null;
  error?: string;
  onChange: (icon: string | null) => void;
}) {
  return (
    <Select
      label="Icon"
      placeholder="Choose"
      data={icons}
      value={value}
      error={error}
      allowDeselect={false}
      renderOption={renderIcon}
      leftSection={value ? <ItemIcon icon={value} /> : null}
      inputWrapperOrder={BELOW}
      onChange={onChange}
      w={170}
    />
  );
}

const BELOW = ['label', 'input', 'description', 'error'] as ('label' | 'input' | 'description' | 'error')[];

function cents(euros: number | string): number {
  return Math.round(Number(euros) * 100);
}

function useListChange<Input>(
  queryKey: readonly unknown[],
  run: (input: Input) => Promise<PriceList>,
  salesKey?: readonly unknown[],
) {
  const client = useQueryClient();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const mutation = useMutation({
    mutationFn: run,
    onSuccess: async (list) => {
      client.setQueryData(queryKey, list);
      setErrors({});
      if (salesKey) await client.invalidateQueries({ queryKey: salesKey });
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  return { mutation, errors, setErrors };
}

function Row({
  item,
  icons,
  first,
  last,
  queryKey,
  salesKey,
  change,
  remove,
  shown,
  columns,
}: {
  item: Item;
  icons: Icon[];
  first: boolean;
  last: boolean;
  queryKey: readonly unknown[];
  salesKey?: readonly unknown[];
  change: PriceListProps['change'];
  remove: PriceListProps['remove'];
  /** Its figures, when the list shows them. */
  shown: { sold: number; made: number } | null;
  columns: number;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(item.name);
  const [icon, setIcon] = useState<string | null>(item.icon);
  const [price, setPrice] = useState<number | string>(item.price_cents / 100);
  const { mutation, errors } = useListChange(queryKey, (input: ItemChange) => change(item.id, input));
  const deletion = useListChange(queryKey, () => remove(item.id), salesKey);
  // Asked once more: the item leaves the list, and every sales point's menu.
  const [deleting, setDeleting] = useState(false);
  if (editing) {
    return (
      <Table.Tr>
        <Table.Td colSpan={columns}>
          <Group align="flex-start" gap="sm" wrap="wrap">
            <TextInput
              label="Name"
              value={name}
              maxLength={Math.max(NAME_LENGTH, item.name.length)}
              description={<NameCount name={name} />}
              inputWrapperOrder={BELOW}
              error={errors.name}
              onChange={(event) => {
                setName(event.currentTarget.value);
              }}
              style={{ flex: '1 1 12rem' }}
            />
            <NumberInput
              label="Price"
              prefix="€"
              decimalScale={2}
              fixedDecimalScale
              min={0.01}
              allowNegative={false}
              value={price}
              error={errors.price_cents}
              onChange={setPrice}
              w={130}
            />
            <IconField icons={icons} value={icon} error={errors.icon} onChange={setIcon} />
            <Group gap="xs" mt={25}>
              <Button
                size="sm"
                loading={mutation.isPending}
                onClick={() => {
                  mutation.mutate(
                    { name, price_cents: cents(price), icon },
                    {
                      onSuccess: () => {
                        setEditing(false);
                      },
                    },
                  );
                }}
              >
                Save
              </Button>
              <Button
                size="sm"
                variant="default"
                onClick={() => {
                  setEditing(false);
                  setName(item.name);
                  setPrice(item.price_cents / 100);
                  setIcon(item.icon);
                }}
              >
                Cancel
              </Button>
            </Group>
          </Group>
        </Table.Td>
      </Table.Tr>
    );
  }
  return (
    <Table.Tr data-off={item.active ? undefined : true} style={item.active ? undefined : { opacity: 0.55 }}>
      <Table.Td>
        <Group gap="xs" wrap="nowrap">
          <ItemIcon icon={item.icon} />
          <span>{item.name}</span>
        </Group>
      </Table.Td>
      <Table.Td ta="right" className="ja-figures">
        {formatEuros(item.price_cents)}
      </Table.Td>
      {shown ? (
        <>
          <Table.Td ta="right" className="ja-figures">
            {shown.sold}
          </Table.Td>
          <Table.Td ta="right" className="ja-figures">
            {formatEuros(shown.made)}
          </Table.Td>
        </>
      ) : null}
      <Table.Td>
        <Switch
          size="sm"
          checked={item.active}
          aria-label={`Sell ${item.name}`}
          onChange={(event) => {
            mutation.mutate({ active: event.currentTarget.checked });
          }}
        />
      </Table.Td>
      <Table.Td>
        <Group gap={4} justify="flex-end" wrap="nowrap">
          <Tooltip label="Change">
            <ActionIcon
              variant="subtle"
              color="gray"
              aria-label={`Change ${item.name}`}
              onClick={() => {
                setEditing(true);
              }}
            >
              <IconPencil size={16} />
            </ActionIcon>
          </Tooltip>
          <ActionIcon
            variant="subtle"
            color="gray"
            aria-label={`Move ${item.name} up`}
            disabled={first}
            onClick={() => {
              mutation.mutate({ move: -1 });
            }}
          >
            <IconArrowUp size={16} />
          </ActionIcon>
          <ActionIcon
            variant="subtle"
            color="gray"
            aria-label={`Move ${item.name} down`}
            disabled={last}
            onClick={() => {
              mutation.mutate({ move: 1 });
            }}
          >
            <IconArrowDown size={16} />
          </ActionIcon>
          {deleting ? (
            <Button
              size="compact-sm"
              color="red"
              loading={deletion.mutation.isPending}
              onClick={() => {
                deletion.mutation.mutate(undefined);
              }}
              onBlur={() => {
                setDeleting(false);
              }}
            >
              Yes, delete
            </Button>
          ) : (
            <Tooltip label="Delete">
              <ActionIcon
                variant="subtle"
                color="gray"
                aria-label={`Delete ${item.name}`}
                onClick={() => {
                  setDeleting(true);
                }}
              >
                <IconTrash size={16} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

/** What was sold of an item no longer on the list: its figures, greyed. */
function GoneRow({ found, period }: { found: ItemSales; period: Period }) {
  const shown = figures(found, period);
  return (
    <Table.Tr style={{ opacity: 0.55 }}>
      <Table.Td>
        <Group gap="xs" wrap="nowrap">
          <ItemIcon icon={found.icon} />
          <span>{found.name}</span>
        </Group>
      </Table.Td>
      <Table.Td>
        <Text size="sm" c="dimmed">
          No longer on the list
        </Text>
      </Table.Td>
      <Table.Td ta="right" className="ja-figures">
        {shown.sold}
      </Table.Td>
      <Table.Td ta="right" className="ja-figures">
        {formatEuros(shown.made)}
      </Table.Td>
      <Table.Td colSpan={2} />
    </Table.Tr>
  );
}

/** The deleted items as one row, "3 no longer on the list", opening to show each. */
function GoneRows({ gone, period }: { gone: ItemSales[]; period: Period }) {
  const [open, setOpen] = useState(false);
  // In 30 days: those with anything then -- a sale taken back counts too.
  const shown =
    period === '30' ? gone.filter((found) => found.sold_30_days !== 0 || found.earned_30_days !== 0) : gone;
  if (!shown.length) return null;
  const sold = shown.reduce((sum, found) => sum + figures(found, period).sold, 0);
  const made = shown.reduce((sum, found) => sum + figures(found, period).made, 0);
  return (
    <>
      <Table.Tr style={{ opacity: 0.55 }}>
        <Table.Td colSpan={2}>
          <UnstyledButton
            aria-expanded={open}
            onClick={() => {
              setOpen(!open);
            }}
          >
            <Group gap="xs" wrap="nowrap">
              <IconChevronRight
                size={14}
                aria-hidden
                style={{ transform: open ? 'rotate(90deg)' : undefined, transition: 'transform 0.15s' }}
              />
              <Text size="sm">{`${String(shown.length)} no longer on the list`}</Text>
            </Group>
          </UnstyledButton>
        </Table.Td>
        <Table.Td ta="right" className="ja-figures">
          {sold}
        </Table.Td>
        <Table.Td ta="right" className="ja-figures">
          {formatEuros(made)}
        </Table.Td>
        <Table.Td colSpan={2} />
      </Table.Tr>
      {open
        ? shown.map((found) => <GoneRow key={`gone-${found.name}`} found={found} period={period} />)
        : null}
    </>
  );
}

function AddItem({
  icons,
  queryKey,
  add,
}: {
  icons: Icon[];
  queryKey: readonly unknown[];
  add: PriceListProps['add'];
}) {
  const [name, setName] = useState('');
  const [price, setPrice] = useState<number | string>('');
  const [icon, setIcon] = useState<string | null>(null);
  const { mutation, errors, setErrors } = useListChange(queryKey, add);
  const ready = name.trim() !== '' && price !== '' && Number(price) > 0 && icon !== null;
  return (
    <Group align="flex-start" gap="sm" wrap="wrap" px="md" pb="md">
      <TextInput
        label="New item"
        placeholder="Beer"
        maxLength={NAME_LENGTH}
        description={<NameCount name={name} />}
        value={name}
        error={errors.name}
        inputWrapperOrder={BELOW}
        onChange={(event) => {
          setName(event.currentTarget.value);
          setErrors({});
        }}
        style={{ flex: '1 1 12rem' }}
      />
      <NumberInput
        label="Price"
        prefix="€"
        decimalScale={2}
        fixedDecimalScale
        min={0.01}
        allowNegative={false}
        placeholder="€2.00"
        value={price}
        error={errors.price_cents}
        inputWrapperOrder={BELOW}
        onChange={(value) => {
          setPrice(value);
          setErrors({});
        }}
        w={130}
      />
      <IconField
        icons={icons}
        value={icon}
        error={errors.icon}
        onChange={(value) => {
          setIcon(value);
          setErrors({});
        }}
      />
      <Tooltip label="Add">
        <ActionIcon
          size={36}
          mt={25}
          aria-label="Add"
          disabled={!ready}
          loading={mutation.isPending}
          onClick={() => {
            mutation.mutate(
              { name, price_cents: cents(price), icon: icon ?? '' },
              {
                onSuccess: () => {
                  setName('');
                  setPrice('');
                  setIcon(null);
                },
              },
            );
          }}
        >
          <IconPlus size={18} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}

export function PriceListPanel({
  title,
  list,
  queryKey,
  add,
  change,
  remove,
  sales,
  salesKey,
}: PriceListProps) {
  const [period, setPeriod] = useState<Period>('30');
  const byItem = new Map((sales?.items ?? []).map((found) => [found.item_id, found]));
  const gone = (sales?.items ?? []).filter((found) => !found.listed);
  const columns = sales ? 6 : 4;
  const together = sales
    ? period === '30'
      ? { sold: sales.sold_30_days, made: sales.earned_30_days }
      : { sold: sales.sold, made: sales.earned }
    : null;
  return (
    <Panel
      title={title}
      icon={IconTag}
      flush
      actions={
        sales ? (
          <SegmentedControl
            size="xs"
            aria-label="Sold and made"
            value={period}
            data={[
              { value: '30', label: '30 days' },
              { value: 'ever', label: 'Ever' },
            ]}
            onChange={(value) => {
              setPeriod(value);
            }}
          />
        ) : null
      }
    >
      <Stack gap={0}>
        {!list.enabled ? (
          <Text size="sm" c="dimmed" px="md" pt="md">
            Credit is switched off: nothing is sold until it is on.
          </Text>
        ) : null}
        {list.items.length || gone.length ? (
          <Table.ScrollContainer minWidth={sales ? 600 : 420} type="native">
            <Table verticalSpacing="sm" aria-label={title}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Item</Table.Th>
                  <Table.Th scope="col" ta="right">
                    Price
                  </Table.Th>
                  {sales ? (
                    <>
                      <Table.Th scope="col" ta="right">
                        Sold
                      </Table.Th>
                      <Table.Th scope="col" ta="right">
                        Made
                      </Table.Th>
                    </>
                  ) : null}
                  <Table.Th scope="col">On sale</Table.Th>
                  <Table.Th scope="col">
                    <VisuallyHidden>Change</VisuallyHidden>
                  </Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {list.items.map((item, index) => (
                  <Row
                    key={`${String(item.id)}-${item.name}-${String(item.price_cents)}-${item.icon}`}
                    item={item}
                    icons={list.icons}
                    first={index === 0}
                    last={index === list.items.length - 1}
                    queryKey={queryKey}
                    salesKey={salesKey}
                    change={change}
                    remove={remove}
                    shown={sales ? figures(byItem.get(item.id), period) : null}
                    columns={columns}
                  />
                ))}
                {sales ? <GoneRows gone={gone} period={period} /> : null}
              </Table.Tbody>
              {together ? (
                <Table.Tfoot>
                  <Table.Tr>
                    <Table.Th scope="row" colSpan={2}>
                      Together
                    </Table.Th>
                    <Table.Th ta="right" className="ja-figures">
                      {together.sold}
                    </Table.Th>
                    <Table.Th ta="right" className="ja-figures">
                      {formatEuros(together.made)}
                    </Table.Th>
                    <Table.Th colSpan={2} />
                  </Table.Tr>
                </Table.Tfoot>
              ) : null}
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Stack px="md">
            <EmptyState>Nothing on the list yet.</EmptyState>
          </Stack>
        )}
        <AddItem icons={list.icons} queryKey={queryKey} add={add} />
      </Stack>
    </Panel>
  );
}
