/**
 * A figure that runs to its new value rather than jumping to it: the credit
 * counting up as a top-up arrives. The first value is simply there (nothing
 * to count from); with reduced motion asked for, every value is.
 */
import { useEffect, useState } from 'react';

export function prefersLessMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/** Fast at first, settling at the end. */
export function eased(progress: number): number {
  return 1 - (1 - progress) ** 3;
}

export function useCountUp(value: number, durationMs = 900): number {
  const [run, setRun] = useState({ from: value, to: value, shown: value });
  // A new value: count to it from what is on screen now (state from the
  // render before, set while rendering, as React allows for this).
  if (run.to !== value) setRun({ from: run.shown, to: value, shown: run.shown });
  const { from, to } = run;
  useEffect(() => {
    if (from === to || prefersLessMotion()) return undefined;
    let frame = 0;
    const began = performance.now();
    const step = (now: number) => {
      const progress = Math.min(1, (now - began) / durationMs);
      const shown = Math.round(from + (to - from) * eased(progress));
      setRun((current) =>
        current.to === to ? { from: progress === 1 ? to : current.from, to, shown } : current,
      );
      if (progress < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(frame);
    };
  }, [from, to, durationMs]);
  return prefersLessMotion() ? value : run.shown;
}
