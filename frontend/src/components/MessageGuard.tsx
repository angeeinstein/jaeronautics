/**
 * What keeps bots out of the message forms (services/messages.py), without a
 * captcha service: a field people never see and bots fill in, and the time
 * between opening the form and sending it -- faster than anybody types is
 * refused on the server.
 */
import { TextInput } from '@mantine/core';
import { useState } from 'react';

/** Seconds since the form was first drawn, read when it is sent. */
export function useWritingTime() {
  const [opened] = useState(() => Date.now());
  return () => (Date.now() - opened) / 1000;
}

/** The field nobody sees: off the screen, out of the tab order, unlabelled for readers. */
export function TrapField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <div aria-hidden style={{ position: 'absolute', left: -10000, width: 1, height: 1, overflow: 'hidden' }}>
      <TextInput
        label="Website"
        name="website"
        tabIndex={-1}
        autoComplete="off"
        value={value}
        onChange={(event) => {
          onChange(event.currentTarget.value);
        }}
      />
    </div>
  );
}
