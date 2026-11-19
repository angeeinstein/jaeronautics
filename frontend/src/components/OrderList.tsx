/**
 * An order (docs/design.md, "An order"): things tried one after another, top
 * first, reordered by dragging a row's handle -- also by keyboard (the handle
 * focused, Space, the arrows, Space) and by the up and down buttons on each
 * row, for a phone. Every movement is still with reduced motion.
 */
import { ActionIcon, Group, Stack, Text } from '@mantine/core';
import { useReducedMotion } from '@mantine/hooks';
import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { IconArrowDown, IconArrowUp, IconGripVertical } from '@tabler/icons-react';
import type { ReactNode } from 'react';

import classes from './OrderList.module.css';

export interface OrderItem {
  value: string;
  label: string;
  /** Under the label, muted: whether it is set up, say. */
  note?: ReactNode;
}

function Row({
  item,
  position,
  count,
  onMove,
}: {
  item: OrderItem;
  position: number;
  count: number;
  onMove: (to: number) => void;
}) {
  const reduceMotion = useReducedMotion();
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id: item.value });
  return (
    <li
      ref={setNodeRef}
      className={classes.row}
      data-dragging={isDragging || undefined}
      style={{
        transform: CSS.Translate.toString(transform),
        transition: reduceMotion ? undefined : transition,
      }}
    >
      <Group gap="sm" wrap="nowrap">
        <ActionIcon
          ref={setActivatorNodeRef}
          variant="subtle"
          color="gray"
          className={classes.handle}
          aria-label={`Drag ${item.label}`}
          {...attributes}
          {...listeners}
        >
          <IconGripVertical size={18} />
        </ActionIcon>
        <Text className={classes.position} aria-hidden>
          {position + 1}
        </Text>
        <Stack gap={0} className={classes.label}>
          <Text fw={500}>{item.label}</Text>
          {item.note ? (
            <Text size="xs" c="dimmed">
              {item.note}
            </Text>
          ) : null}
        </Stack>
        <ActionIcon
          variant="default"
          aria-label={`Move ${item.label} up`}
          disabled={position === 0}
          onClick={() => {
            onMove(position - 1);
          }}
        >
          <IconArrowUp size={16} />
        </ActionIcon>
        <ActionIcon
          variant="default"
          aria-label={`Move ${item.label} down`}
          disabled={position === count - 1}
          onClick={() => {
            onMove(position + 1);
          }}
        >
          <IconArrowDown size={16} />
        </ActionIcon>
      </Group>
    </li>
  );
}

export function OrderList({
  items,
  label,
  onChange,
}: {
  items: OrderItem[];
  /** What the list orders, for screen readers: "Order the services are tried in". */
  label: string;
  /** The values in their new order. */
  onChange: (values: string[]) => void;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const values = items.map((item) => item.value);
  const move = (from: number, to: number) => {
    if (to < 0 || to >= values.length || from === to) return;
    onChange(arrayMove(values, from, to));
  };
  const dropped = ({ active, over }: DragEndEvent) => {
    if (!over) return;
    move(values.indexOf(String(active.id)), values.indexOf(String(over.id)));
  };
  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={dropped}>
      <SortableContext items={values} strategy={verticalListSortingStrategy}>
        <ol className={classes.list} aria-label={label}>
          {items.map((item, position) => (
            <Row
              key={item.value}
              item={item}
              position={position}
              count={items.length}
              onMove={(to) => {
                move(position, to);
              }}
            />
          ))}
        </ol>
      </SortableContext>
    </DndContext>
  );
}
