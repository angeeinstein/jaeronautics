/**
 * The start page (/), for somebody not signed in -- Flask sends anybody
 * signed in on to their own start page. Not a second homepage of the
 * association (that is the website, in the footer): what this portal is for,
 * and the two ways in.
 */
import { Button, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';

import { AppLink } from '../../app/AppLink';
import { useDocumentTitle } from '../../components/PageHeader';
import classes from './Landing.module.css';

const POINTS = [
  ['Join', 'Sign up in a few minutes and pay the membership fee online.'],
  ['Your membership', 'Contact details, payments and renewal: all in your account, whenever you need them.'],
  ['The forum', 'Members’ discussions, with the account you already have here. No second password.'],
] as const;

export function Landing() {
  useDocumentTitle('Members’ portal');
  return (
    <Stack gap="xl">
      <Stack gap="md" className={classes.hero}>
        <Text className={classes.eyebrow}>Members’ portal</Text>
        <Title order={1}>Your membership, in one place</Title>
        <Text size="lg" c="dimmed" className={classes.lead}>
          Join Joanneum Aeronautics, keep your details and payments up to date, and find your way into the
          members’ forum.
        </Text>
        <Group justify="center" gap="sm" mt="sm">
          <Button component={AppLink} to="/join" size="md">
            Become a member
          </Button>
          <Button component={AppLink} to="/login" size="md" variant="default">
            Sign in
          </Button>
        </Group>
      </Stack>
      <SimpleGrid component="ol" cols={{ base: 1, sm: 3 }} spacing="md" className={classes.points}>
        {POINTS.map(([title, text], index) => (
          <li key={title} className={classes.point}>
            <span className={classes.number} aria-hidden="true">
              {String(index + 1).padStart(2, '0')}
            </span>
            <Title order={2} size="h4" mb={4}>
              {title}
            </Title>
            <Text size="sm" c="dimmed">
              {text}
            </Text>
          </li>
        ))}
      </SimpleGrid>
      <Text size="sm" c="dimmed" ta="center">
        Were you on the old forum? Join with your university email address, and your old forum account comes
        back with your posts.
      </Text>
    </Stack>
  );
}
