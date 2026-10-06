/**
 * A search field a list follows as it is typed into, a moment after typing
 * stops. What it searches for lives in the page's address, so a change from
 * elsewhere (Reset, the back button) is shown here too.
 */
import { TextInput } from '@mantine/core';
import { useDebouncedCallback } from '@mantine/hooks';
import { IconSearch } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';

interface SearchFieldProps {
  value: string;
  onSearch: (q: string) => void;
  /** The field's name for screen readers, e.g. "Search accounts". */
  label: string;
  placeholder: string;
  className?: string;
}

export function SearchField({ value, onSearch, label, placeholder, className }: SearchFieldProps) {
  const [text, setText] = useState(value);
  const sent = useRef(value);
  const send = useDebouncedCallback((q: string) => {
    sent.current = q.trim();
    onSearch(q.trim());
  }, 300);

  useEffect(() => {
    if (value !== sent.current) {
      sent.current = value;
      setText(value);
    }
  }, [value]);

  return (
    <TextInput
      className={className}
      leftSection={<IconSearch size={16} aria-hidden />}
      placeholder={placeholder}
      aria-label={label}
      value={text}
      onChange={(event) => {
        setText(event.currentTarget.value);
        send(event.currentTarget.value);
      }}
    />
  );
}
