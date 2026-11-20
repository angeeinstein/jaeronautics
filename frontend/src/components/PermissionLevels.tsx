/**
 * Permissions as rows with levels, grouped by area (docs/permissions-plan.md,
 * "The page with levels"): each row a short name, a line saying more, and its
 * levels side by side -- "None · View · Manage", "None · Install". A level
 * that only shows is tinted, one that changes something filled. Marked:
 * *Sensitive* rows, and a level above or below what the roles give.
 * Used by a person's Access tab and a team member's page.
 */
import { Group, Stack, Text } from '@mantine/core';
import { IconEye, IconPencil } from '@tabler/icons-react';

import { groupByArea, type Levels } from '../lib/permissionLevels';
import { Pill } from './Pill';
import classes from './PermissionLevels.module.css';

export interface PermissionLevelRow {
  key: string;
  area: string;
  label: string;
  description: string;
  sensitive: boolean;
  levels: readonly { label: string; sees_only: boolean }[];
  /** Held now. */
  level: number;
  /** The highest level the person looking may set; a row held above it is not theirs to change. */
  may_reach: number;
}

interface Props<R extends PermissionLevelRow> {
  rows: readonly R[];
  levels: Levels;
  /** What the roles give each row: a level above is *added*, one below *lowered* or *taken away*. */
  fromRoles: (row: R) => number;
  onPick: (key: string, level: number) => void;
  /** Nothing can be changed (one's own access, an erased account). */
  locked?: boolean;
  /** A super admin: every row at its top, greyed. */
  everything?: boolean;
}

function marker(level: number, fromRoles: number): string | null {
  if (level > fromRoles) return 'Added';
  if (level < fromRoles) return level === 0 ? 'Taken away' : 'Lowered';
  return null;
}

export function PermissionLevels<R extends PermissionLevelRow>({
  rows,
  levels,
  fromRoles,
  onPick,
  locked = false,
  everything = false,
}: Props<R>) {
  return (
    <Stack gap="lg">
      {groupByArea(rows).map(([area, listed]) => (
        <section key={area} aria-label={area}>
          <Group gap={8} className={classes.area}>
            <Text component="h4" className={classes.areaName}>
              {area}
            </Text>
            <Text size="xs" c="dimmed">
              {`${String(everything ? listed.length : listed.filter((row) => (levels[row.key] ?? 0) > 0).length)} of ${String(listed.length)}`}
            </Text>
          </Group>
          {listed.map((row) => {
            const level = everything ? row.levels.length : (levels[row.key] ?? 0);
            const mark = everything ? null : marker(level, fromRoles(row));
            const outOfReach = locked || everything || row.level > row.may_reach;
            return (
              <div key={row.key} className={classes.row}>
                <div className={classes.text}>
                  <Group gap={8}>
                    <Text fw={500}>{row.label}</Text>
                    {row.sensitive ? <Pill tone="pending">Sensitive</Pill> : null}
                    {mark ? <Pill tone={mark === 'Added' ? 'info' : 'neutral'}>{mark}</Pill> : null}
                  </Group>
                  <Text size="sm" c="dimmed">
                    {row.description}
                  </Text>
                </div>
                <div role="radiogroup" aria-label={row.label} className={classes.levels}>
                  {[{ label: 'None', sees_only: false }, ...row.levels].map((step, index) => {
                    const kind = index === 0 ? 'none' : step.sees_only ? 'view' : 'change';
                    const Icon = kind === 'view' ? IconEye : kind === 'change' ? IconPencil : null;
                    return (
                      <button
                        key={step.label}
                        type="button"
                        role="radio"
                        aria-checked={index === level}
                        data-on={index === level || undefined}
                        data-kind={kind}
                        disabled={outOfReach || index > row.may_reach}
                        className={classes.level}
                        onClick={() => {
                          onPick(row.key, index);
                        }}
                      >
                        {Icon ? <Icon size={14} stroke={2} aria-hidden /> : null}
                        {step.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </section>
      ))}
    </Stack>
  );
}
