/**
 * A seller's price list for credit (docs/credit-plan.md, "Selling"): the
 * association's (Admin › Credit) or a team's (its Prices page). Each item has
 * a name, a price and an icon -- the picture beside it on a sales point's
 * screen, chosen from a fixed list; it can be renamed, repriced, given
 * another icon, moved or switched off -- never deleted, since the sales made
 * still name it. A new price counts for the next sale; sales made keep theirs.
 */
import {
  ActionIcon,
  Button,
  Group,
  NumberInput,
  Select,
  type SelectProps,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Tooltip,
  VisuallyHidden,
} from '@mantine/core';
import { IconArrowDown, IconArrowUp, IconPencil, IconPlus } from '@tabler/icons-react';
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

interface PriceListProps {
  title: string;
  list: PriceList;
  queryKey: readonly unknown[];
  add: (item: NewItem) => Promise<PriceList>;
  change: (id: number, change: ItemChange) => Promise<PriceList>;
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

function useListChange<Input>(queryKey: readonly unknown[], run: (input: Input) => Promise<PriceList>) {
  const client = useQueryClient();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const mutation = useMutation({
    mutationFn: run,
    onSuccess: (list) => {
      client.setQueryData(queryKey, list);
      setErrors({});
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
  change,
}: {
  item: Item;
  icons: Icon[];
  first: boolean;
  last: boolean;
  queryKey: readonly unknown[];
  change: PriceListProps['change'];
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(item.name);
  const [icon, setIcon] = useState<string | null>(item.icon);
  const [price, setPrice] = useState<number | string>(item.price_cents / 100);
  const { mutation, errors } = useListChange(queryKey, (input: ItemChange) => change(item.id, input));
  if (editing) {
    return (
      <Table.Tr>
        <Table.Td colSpan={4}>
          <Group align="flex-start" gap="sm" wrap="wrap">
            <TextInput
              label="Name"
              value={name}
              maxLength={60}
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
        </Group>
      </Table.Td>
    </Table.Tr>
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
        maxLength={60}
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

export function PriceListPanel({ title, list, queryKey, add, change }: PriceListProps) {
  return (
    <Panel title={title} flush>
      <Stack gap={0}>
        {!list.enabled ? (
          <Text size="sm" c="dimmed" px="md" pt="md">
            Credit is switched off: nothing is sold until it is on.
          </Text>
        ) : null}
        {list.items.length ? (
          <Table.ScrollContainer minWidth={420} type="native">
            <Table verticalSpacing="sm" aria-label={title}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Item</Table.Th>
                  <Table.Th scope="col" ta="right">
                    Price
                  </Table.Th>
                  <Table.Th scope="col">Sold</Table.Th>
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
                    change={change}
                  />
                ))}
              </Table.Tbody>
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
