/**
 * The year group, picked rather than typed: the study programme -- the usual
 * ones by name, any other by its three-letter code -- and the year it was
 * started, which together make it ("LAV" and 2025: "LAV25"). Used when
 * joining and when asking for a change of it. The server checks the scheme
 * (three letters, two digits) again; the programmes offered by name come from
 * GET /api/v1/forms/options.
 */
import { Chip, Group, Radio, Select, Stack, Text, TextInput } from '@mantine/core';
import { useState } from 'react';

import type { Schemas } from '../api/client';
import classes from './YearGroupPicker.module.css';

type Programme = Schemas['ProgrammeOut'];

const OTHER = 'other';
const CODE = /^[A-Z]{3}$/;
const YEAR_GROUP = /^([A-Z]{3})([0-9]{2})$/;
/** How many recent years are a tap away; the rest are in "Earlier". */
const RECENT = 6;
const UNKNOWN = 'unknown';
const EARLIER = 'earlier';

export function isYearGroup(value: string) {
  return YEAR_GROUP.test(value);
}

function initial(value: string, programmes: Programme[], thisYear: number) {
  const match = YEAR_GROUP.exec(value);
  if (!match) return { programme: '', other: '', year: '' };
  const [, code = '', digits = ''] = match;
  // Two digits back to a year: the century that is not in the future.
  const century = Math.floor(thisYear / 100) * 100;
  const year =
    century + Number(digits) > thisYear ? century - 100 + Number(digits) : century + Number(digits);
  const known = programmes.some((programme) => programme.code === code);
  return { programme: known ? code : OTHER, other: known ? '' : code, year: String(year) };
}

export function YearGroupPicker({
  value,
  onChange,
  programmes,
  required,
  error,
}: {
  value: string;
  onChange: (yearGroup: string) => void;
  programmes: Programme[];
  /** A student's is; an alumnus may not remember theirs. */
  required: boolean;
  error?: string | null;
}) {
  const thisYear = new Date().getFullYear();
  const [state, setState] = useState(() => initial(value, programmes, thisYear));
  const [unknown, setUnknown] = useState(!required && !value);
  const recent = Array.from({ length: RECENT }, (_, index) => String(thisYear - index));
  const earlier = Array.from({ length: thisYear - RECENT - 1969 }, (_, index) =>
    String(thisYear - RECENT - index),
  );
  const yearIsEarlier = state.year !== '' && !recent.includes(state.year);
  const [showEarlier, setShowEarlier] = useState(yearIsEarlier);

  const code = state.programme === OTHER ? state.other : state.programme;
  const codeProblem = state.programme === OTHER && state.other !== '' && !CODE.test(state.other);
  const composed = CODE.test(code) && state.year ? `${code}${state.year.slice(-2)}` : '';

  const update = (next: Partial<typeof state>) => {
    const merged = { ...state, ...next };
    setState(merged);
    setUnknown(false);
    const mergedCode = merged.programme === OTHER ? merged.other : merged.programme;
    onChange(CODE.test(mergedCode) && merged.year ? `${mergedCode}${merged.year.slice(-2)}` : '');
  };

  const chip = unknown ? UNKNOWN : showEarlier ? EARLIER : state.year;

  return (
    <Stack gap="md">
      <Radio.Group
        label="Programme"
        required={required}
        value={unknown ? null : state.programme || null}
        onChange={(programme) => {
          update({ programme });
        }}
      >
        <div className={classes.programmes}>
          {programmes.map((programme) => (
            <Radio.Card key={programme.code} value={programme.code} radius={0} className={classes.programme}>
              <span className={classes.name}>{`${programme.name} · ${programme.degree}`}</span>
              <span className={classes.code}>{programme.code}</span>
            </Radio.Card>
          ))}
          <Radio.Card value={OTHER} radius={0} className={classes.programme}>
            <span className={classes.name}>Another programme</span>
            <span className={classes.hint}>By its three-letter code</span>
          </Radio.Card>
        </div>
      </Radio.Group>

      {state.programme === OTHER && !unknown ? (
        <TextInput
          label="Programme code"
          description="Three letters, as at the start of your year group."
          required={required}
          placeholder="ABC"
          maxLength={3}
          autoCapitalize="characters"
          w={{ base: '100%', xs: 220 }}
          classNames={{ input: classes.mono }}
          value={state.other}
          error={codeProblem ? 'Three letters, like LAV.' : null}
          onChange={(event) => {
            update({ other: event.currentTarget.value.toUpperCase().replace(/[^A-Z]/g, '') });
          }}
        />
      ) : null}

      <Stack gap={6} component="fieldset" className={classes.fieldset}>
        <Text component="legend" size="sm" fw={500} p={0} mb={6}>
          Started in
          {required ? (
            <Text span c="red" aria-hidden>
              {' *'}
            </Text>
          ) : null}
        </Text>
        <Chip.Group
          multiple={false}
          value={chip}
          onChange={(picked) => {
            if (picked === UNKNOWN) {
              setUnknown(true);
              setShowEarlier(false);
              setState({ ...state, year: '' });
              onChange('');
            } else if (picked === EARLIER) {
              setShowEarlier(true);
              update({ year: '' });
            } else {
              setShowEarlier(false);
              update({ year: picked });
            }
          }}
        >
          <Group gap="xs">
            {recent.map((year) => (
              <Chip key={year} value={year} radius={0}>
                {year}
              </Chip>
            ))}
            <Chip value={EARLIER} radius={0}>
              Earlier
            </Chip>
            {required ? null : (
              <Chip value={UNKNOWN} radius={0}>
                I don’t remember
              </Chip>
            )}
          </Group>
        </Chip.Group>
        {showEarlier ? (
          <Select
            label="Year"
            data={earlier}
            searchable
            w={{ base: '100%', xs: 220 }}
            value={yearIsEarlier ? state.year : null}
            onChange={(year) => {
              update({ year: year ?? '' });
            }}
          />
        ) : null}
      </Stack>

      <Text size="sm" aria-live="polite" c={error ? 'red' : 'dimmed'}>
        {error ??
          (unknown ? (
            'No year group: fine for an alumnus.'
          ) : composed ? (
            <>
              Year group: <span className={classes.result}>{composed}</span>
            </>
          ) : (
            'Choose the programme and the year you started.'
          ))}
      </Text>
    </Stack>
  );
}
