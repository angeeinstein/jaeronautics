/**
 * How somebody pays, asked before Stripe's payment page opens
 * (docs/frontend-structure.md, "How somebody pays"): card -- it counts
 * at once -- or SEPA Direct Debit, which counts only once the debit clears,
 * days later. A choice to click, since a line beside the button is not read.
 * Both answers open the same Stripe page with both methods: whoever changes
 * their mind there has read what it means.
 *
 * Joining asks on its last step (``PayMethodChoice``); starting a membership
 * again, resuming a payment and a team's fee ask in a small dialog when the
 * pay button is pressed (``PayButton``). In the year's free months nothing
 * is charged, so a membership does not ask then (``useAsksHowToPay``).
 */
import { type ButtonProps, Button, Loader, Modal, Radio, Stack, Text } from '@mantine/core';
import { IconBuildingBank, IconCheck, IconCreditCard } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useState } from 'react';

import { api, call } from '../../api/client';
import classes from './PayMethod.module.css';

export type PayMethod = 'card' | 'sepa';
/** What is paid for: a membership, a team's fee to join, or one to stay in the team. */
export type PayFor = 'membership' | 'team' | 'team-stay';

const OUTCOME: Record<PayFor, Record<PayMethod, string>> = {
  membership: {
    card: 'Your membership starts right away.',
    sepa: 'Your membership starts once the debit clears, in a few days.',
  },
  team: {
    card: 'Your team membership starts right away.',
    sepa: 'Your team membership starts once the debit clears, in a few days.',
  },
  'team-stay': {
    card: 'Your team membership carries on right away.',
    sepa: 'Your team membership carries on once the debit clears, in a few days.',
  },
};

const METHODS = [
  { value: 'card', name: 'Card, Apple Pay or Google Pay', icon: IconCreditCard },
  { value: 'sepa', name: 'SEPA Direct Debit', icon: IconBuildingBank },
] as const;

export const QUESTION = 'How do you want to pay?';

export const signupPriceQuery = {
  queryKey: ['signup', 'price'] as const,
  queryFn: () => call(api.GET('/api/v1/signup/price')),
  staleTime: 5 * 60_000,
};

/** Whether paying for a membership today asks how: not while nothing is charged today. */
export function useAsksHowToPay(): boolean {
  const price = useQuery(signupPriceQuery).data?.price;
  return !price || !!price.due_today;
}

/** The two ways, as cards to choose from. */
export function PayMethodChoice({
  payFor,
  value,
  onChange,
  error,
  busy = false,
  label,
}: {
  payFor: PayFor;
  value: PayMethod | null;
  onChange: (method: PayMethod) => void;
  error?: ReactNode;
  /** The chosen one is on its way to Stripe: a spinner on it, the other one off. */
  busy?: boolean;
  label?: ReactNode;
}) {
  return (
    <Radio.Group
      value={value}
      onChange={(next) => {
        onChange(next === 'sepa' ? 'sepa' : 'card');
      }}
      label={label}
      aria-label={label ? undefined : QUESTION}
      error={error}
      classNames={{ label: classes.question }}
    >
      <div className={classes.methods}>
        {METHODS.map(({ value: method, name, icon: Icon }) => {
          const checked = method === value;
          return (
            <Radio.Card
              key={method}
              value={method}
              radius={0}
              className={classes.method}
              data-checked={checked || undefined}
              disabled={busy && !checked}
              onClick={() => {
                // Chosen again: on, as the choice already stands (the dialog goes on with it).
                if (checked && !busy) onChange(method);
              }}
            >
              <span className={classes.icon} aria-hidden>
                <Icon size={22} stroke={1.8} />
              </span>
              <Stack gap={2}>
                <span className={classes.name}>{name}</span>
                <Text size="sm" c="dimmed">
                  {OUTCOME[payFor][method]}
                </Text>
              </Stack>
              {checked ? (
                <span className={classes.tick} aria-hidden>
                  {busy ? (
                    <Loader size={14} color="var(--ja-accent-text)" />
                  ) : (
                    <IconCheck size={15} stroke={3} />
                  )}
                </span>
              ) : null}
            </Radio.Card>
          );
        })}
      </div>
    </Radio.Group>
  );
}

/**
 * A button that opens Stripe's page through ``onPay``: when ``ask``, first a
 * dialog with the two ways, and choosing one goes on at once.
 */
export function PayButton({
  payFor,
  ask,
  loading,
  onPay,
  children,
  ...button
}: {
  payFor: PayFor;
  ask: boolean;
  loading: boolean;
  onPay: () => void;
  children: ReactNode;
} & Omit<ButtonProps, 'loading' | 'onClick' | 'children'>) {
  const [opened, setOpened] = useState(false);
  const [method, setMethod] = useState<PayMethod | null>(null);
  return (
    <>
      <Button
        {...button}
        loading={loading && !opened}
        onClick={() => {
          if (!ask) {
            onPay();
            return;
          }
          setMethod(null);
          setOpened(true);
        }}
      >
        {children}
      </Button>
      <Modal
        opened={opened}
        onClose={() => {
          if (!loading) setOpened(false);
        }}
        title={QUESTION}
        centered
        size="md"
      >
        <PayMethodChoice
          payFor={payFor}
          value={method}
          busy={loading}
          onChange={(chosen) => {
            setMethod(chosen);
            onPay();
          }}
        />
      </Modal>
    </>
  );
}
