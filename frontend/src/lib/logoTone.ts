/**
 * Whether a team's logo is light or dark, so the soft glow around it can be
 * the other: a dark glow lifts a light logo off a light photo, a light glow
 * a dark one off a dark photo. Measured once per logo from its own pixels
 * -- the visible ones only, so a transparent background or gaps in the logo
 * count for nothing. Logos come from the portal itself, so the canvas may
 * read them.
 */
import { useEffect, useState } from 'react';

export type LogoTone = 'light' | 'dark';

const known = new Map<string, LogoTone>();

/** The tone of the pixels that can be seen, or null where there are none. */
export function toneOf(pixels: Uint8ClampedArray): LogoTone | null {
  let weight = 0;
  let light = 0;
  for (let index = 0; index < pixels.length; index += 4) {
    const alpha = (pixels[index + 3] ?? 0) / 255;
    if (alpha < 0.1) continue;
    const [red = 0, green = 0, blue = 0] = [pixels[index], pixels[index + 1], pixels[index + 2]];
    // Relative luminance, near enough for light or dark.
    light += alpha * ((0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255);
    weight += alpha;
  }
  if (weight === 0) return null;
  return light / weight > 0.55 ? 'light' : 'dark';
}

function measure(url: string): Promise<LogoTone | null> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => {
      const size = 48;
      const canvas = document.createElement('canvas');
      canvas.width = size;
      canvas.height = size;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      if (!context) {
        resolve(null);
        return;
      }
      try {
        context.drawImage(image, 0, 0, size, size);
        resolve(toneOf(context.getImageData(0, 0, size, size).data));
      } catch {
        resolve(null); // a picture the browser will not let the page read
      }
    };
    image.onerror = () => {
      resolve(null);
    };
    image.src = url;
  });
}

/** The logo's tone once measured; null before, or when it cannot be told. */
export function useLogoTone(url: string | null): LogoTone | null {
  // Measured once per address; the answer for this one, kept here only to
  // draw again once it arrives.
  const [measured, setMeasured] = useState<{ url: string; tone: LogoTone | null } | null>(null);
  useEffect(() => {
    if (!url || known.has(url)) return;
    let current = true;
    void measure(url).then((tone) => {
      if (tone) known.set(url, tone);
      if (current) setMeasured({ url, tone });
    });
    return () => {
      current = false;
    };
  }, [url]);
  if (!url) return null;
  return known.get(url) ?? (measured?.url === url ? measured.tone : null);
}
