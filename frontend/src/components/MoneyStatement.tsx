/**
 * How a team's money adds up (services/team_money.py, ``statement``): each kind
 * of line added up -- fees paid, refunds, Stripe's fees, credit sales and
 * their share of top-up fees, what the association keeps -- down to what the
 * team earned, less what was transferred, to what is open. Below, every line
 * it adds up from, a payment's or a sale's lines kept together. Shown alike
 * on the team's own Money page and on Admin › Money › the team.
 */
import { Button, Collapse, Stack, Table, Text } from '@mantine/core';
import { useState } from 'react';

import type { Schemas } from '../api/client';
import { formatDate, formatEuros } from '../lib/format';
import classes from './MoneyStatement.module.css';
import { Panel } from './Panel';
import { Pill } from './Pill';

type Statement = Schemas['Statement'];
type Line = Statement['lines'][number];

function Amount({ cents, strong = false }: { cents: number; strong?: boolean }) {
  return (
    <Table.Td className={`${classes.amount} ja-figures`} fw={strong ? 700 : undefined}>
      {formatEuros(cents)}
    </Table.Td>
  );
}

function Sums({ statement }: { statement: Statement }) {
  return (
    <Table verticalSpacing={6} aria-label="How it adds up">
      <Table.Tbody>
        {statement.rows.map((row) => (
          <Table.Tr key={row.kind}>
            <Table.Th scope="row" fw={400}>
              {row.label}
            </Table.Th>
            <Amount cents={row.amount} />
          </Table.Tr>
        ))}
        <Table.Tr className={classes.total}>
          <Table.Th scope="row">Earned</Table.Th>
          <Amount cents={statement.earned} strong />
        </Table.Tr>
        <Table.Tr>
          <Table.Th scope="row" fw={400}>
            Transferred to the team
          </Table.Th>
          <Amount cents={-statement.transferred || 0} />
        </Table.Tr>
        <Table.Tr className={classes.total}>
          <Table.Th scope="row">Open</Table.Th>
          <Amount cents={statement.open} strong />
        </Table.Tr>
      </Table.Tbody>
    </Table>
  );
}

function Lines({ lines }: { lines: Line[] }) {
  return (
    <Table.ScrollContainer minWidth={520} type="native">
      <Table verticalSpacing={6} aria-label="Every line">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Day</Table.Th>
            <Table.Th scope="col">What</Table.Th>
            <Table.Th scope="col" className={classes.amount}>
              Amount
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {lines.map((line, index) => {
            const first = index === 0 || lines[index - 1]?.group !== line.group;
            return (
              <Table.Tr
                key={`${line.group}-${String(index)}`}
                className={[first && index ? classes.groupStart : '', line.waiting ? classes.waiting : '']
                  .filter(Boolean)
                  .join(' ')}
              >
                <Table.Td className={classes.day}>{first && line.day ? formatDate(line.day) : ''}</Table.Td>
                <Table.Td className={first ? undefined : classes.follow}>
                  {line.text}
                  {line.waiting ? (
                    <>
                      {' '}
                      <Pill tone="neutral">waiting</Pill>
                    </>
                  ) : null}
                  {line.detail ? (
                    <Text size="xs" c="dimmed">
                      {line.detail}
                    </Text>
                  ) : null}
                </Table.Td>
                <Amount cents={line.amount} />
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

export function MoneyStatement({ statement }: { statement: Statement }) {
  const [open, setOpen] = useState(false);
  return (
    <Panel title="How it adds up">
      <Stack gap="sm">
        <Sums statement={statement} />
        <Text size="sm" c="dimmed">
          {statement.teams_carry_fees
            ? "Stripe's fees come off: on each payment, and on each credit sale its share of the fee of the top-up that paid for it."
            : "Stripe's fees are carried by the association."}
          {statement.waiting
            ? ` ${formatEuros(statement.waiting)} counts once Stripe has said its fee, usually within seconds.`
            : ''}
        </Text>
        {statement.lines.length ? (
          <>
            <Collapse expanded={open}>
              <Lines lines={statement.lines} />
            </Collapse>
            <Button
              variant="subtle"
              size="compact-sm"
              w="fit-content"
              aria-expanded={open}
              onClick={() => {
                setOpen(!open);
              }}
            >
              {open ? 'Hide the lines' : `Every line (${String(statement.lines.length)})`}
            </Button>
          </>
        ) : null}
      </Stack>
    </Panel>
  );
}
