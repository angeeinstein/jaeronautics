import { Center } from '@mantine/core';
import { useRouteError } from 'react-router';

import { ErrorState } from '../components/States';

/** A page that failed to render: said plainly, with the way back, never a blank screen. */
export function RouteError() {
  const error = useRouteError();
  return (
    <Center mih="60vh" p="md">
      <ErrorState
        error={error}
        onRetry={() => {
          window.location.reload();
        }}
      />
    </Center>
  );
}
