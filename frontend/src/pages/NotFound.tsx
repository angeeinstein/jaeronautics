import { Button, Center, Stack, Text, Title } from '@mantine/core';

import { AppLink } from '../app/AppLink';

/** An address inside the app that is no page of it. */
export function NotFound() {
  return (
    <Center mih="60vh" p="md">
      <Stack align="center" gap="sm" maw={480} ta="center">
        <Title order={1}>Page not found</Title>
        <Text c="dimmed">This address is not a page of the portal, or not any more.</Text>
        <Button component={AppLink} to="/account" mt="sm">
          To my account
        </Button>
      </Stack>
    </Center>
  );
}
