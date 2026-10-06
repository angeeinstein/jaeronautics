/**
 * The two-step confirm for actions that cannot be undone (deactivate, erase,
 * reject): the first click arms the button -- it turns red and asks again --
 * the second does it. Left alone, it disarms after a few seconds.
 */
import { Button, type ButtonProps } from '@mantine/core';
import { useEffect, useRef, useState } from 'react';

interface ConfirmButtonProps extends Omit<ButtonProps, 'onClick' | 'children'> {
  children: string;
  /** What the armed button says, e.g. "Yes, erase". */
  confirmLabel: string;
  onConfirm: () => void;
  /** How long the armed state lasts. */
  timeoutMs?: number;
}

export function ConfirmButton({
  children,
  confirmLabel,
  onConfirm,
  timeoutMs = 5000,
  ...rest
}: ConfirmButtonProps) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(
    () => () => {
      window.clearTimeout(timer.current);
    },
    [],
  );

  function click() {
    if (!armed) {
      setArmed(true);
      timer.current = window.setTimeout(() => {
        setArmed(false);
      }, timeoutMs);
      return;
    }
    window.clearTimeout(timer.current);
    setArmed(false);
    onConfirm();
  }

  return (
    <Button
      color="red"
      variant={armed ? 'filled' : 'outline'}
      onClick={click}
      onBlur={() => {
        window.clearTimeout(timer.current);
        setArmed(false);
      }}
      aria-live="polite"
      data-armed={armed || undefined}
      {...rest}
    >
      {armed ? confirmLabel : children}
    </Button>
  );
}
