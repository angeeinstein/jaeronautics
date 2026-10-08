/**
 * An address that is no page of the portal -- one inside the app, or any other
 * Flask does not know (it answers 404 with the app, which draws this).
 */
import { Button, Center, Stack, Text, Title } from '@mantine/core';

import { useDocumentTitle } from '../components/PageHeader';

export function NotFound() {
  useDocumentTitle('Page not found');
  return (
    <Center mih="50vh" p="md">
      <Stack align="center" gap="sm" maw={480} ta="center">
        <Title order={1}>Page not found</Title>
        <Text c="dimmed">This address is not a page of the portal, or not any more.</Text>
        {/* A whole page load: Flask sends somebody signed in on to their own start page. */}
        <Button component="a" href="/" mt="sm">
          To the start page
        </Button>
      </Stack>
    </Center>
  );
}
