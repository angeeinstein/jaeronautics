/**
 * One slim line on a team's own page, for its active members while the team
 * records attendance (docs/attendance-plan.md, "Remote work"): this month's
 * time with the team, how much of it was remote in small print, *Add remote
 * work* where the team allows it, and the way to the whole list on My Account.
 * Quiet on purpose -- "this shouldn't look too much like some company where
 * you are forced to work" -- but at the top, so nobody has to look for it.
 *
 * Data: GET /api/v1/account/attendance -- the member's own, this team's panel.
 */
import { Button, Group, Paper, Text } from '@mantine/core';
import { IconChevronRight, IconClockHour4, IconPlus } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, call } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { ACCOUNT_ATTENDANCE_KEY, RemoteWorkDialog } from '../../components/attendance/RemoteWorkForm';
import { remoteMinutes, totalMinutes } from '../../components/attendance/StayTable';
import { attendanceDay, formatMinutes } from '../../lib/format';

export function YourTime({ slug, member }: { slug: string; member: boolean }) {
  const [adding, setAdding] = useState(false);
  const attendance = useQuery({
    queryKey: ACCOUNT_ATTENDANCE_KEY,
    queryFn: () => call(api.GET('/api/v1/account/attendance')),
    enabled: member,
  });
  const owner = attendance.data?.owners.find((item) => item.owner === slug && item.current);
  if (!member || !owner || !attendance.data) return null;
  const month = attendance.data.today.slice(0, 7);
  const stays = owner.stays.filter((stay) => attendanceDay(stay.started_at).slice(0, 7) === month);
  const away = remoteMinutes(stays);
  const all = totalMinutes(stays);
  return (
    <Paper withBorder radius={0} px="md" py="sm">
      <Group gap="sm" justify="space-between" wrap="wrap">
        <Group gap="xs" wrap="wrap" align="baseline">
          <IconClockHour4 size={18} aria-hidden style={{ color: 'var(--ja-muted)', alignSelf: 'center' }} />
          <Text size="sm">Your time this month</Text>
          <Text size="sm" fw={600} className="ja-figures">
            {formatMinutes(all)}
          </Text>
          {owner.remote || away ? (
            <Text size="xs" c="dimmed" className="ja-figures">
              {formatMinutes(all - away)} at the readers · {formatMinutes(away)} remote
            </Text>
          ) : null}
        </Group>
        <Group gap="md" wrap="wrap">
          {owner.remote ? (
            <Button
              size="xs"
              variant="default"
              leftSection={<IconPlus size={14} aria-hidden />}
              onClick={() => {
                setAdding(true);
              }}
            >
              Add remote work
            </Button>
          ) : null}
          <Button
            size="xs"
            variant="subtle"
            component={AppLink}
            to="/account/attendance"
            rightSection={<IconChevronRight size={14} aria-hidden />}
          >
            Your time
          </Button>
        </Group>
      </Group>
      <RemoteWorkDialog
        opened={adding}
        onClose={() => {
          setAdding(false);
        }}
        owner={owner.owner}
        ownerName={owner.name}
        remoteFrom={attendance.data.remote_from}
        today={attendance.data.today}
      />
    </Paper>
  );
}
