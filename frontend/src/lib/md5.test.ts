import { describe, expect, it } from 'vitest';

import { md5Hex } from './md5';

const text = (value: string) => new TextEncoder().encode(value);

describe('md5Hex', () => {
  it.each([
    // RFC 1321's test suite.
    ['', 'd41d8cd98f00b204e9800998ecf8427e'],
    ['a', '0cc175b9c0f1b6a831c399e269772661'],
    ['abc', '900150983cd24fb0d6963f7d28e17f72'],
    ['message digest', 'f96b697d7cb7938d525a2f31aaf161d0'],
    ['abcdefghijklmnopqrstuvwxyz', 'c3fcd3d76192e4007dfb496cca67e13b'],
    [
      '12345678901234567890123456789012345678901234567890123456789012345678901234567890',
      '57edf4a22be3c955ac49da2e2107b67a',
    ],
  ])('%j', (input, expected) => {
    expect(md5Hex(text(input))).toBe(expected);
  });

  it('runs over block borders: 55, 56 and 64 bytes', () => {
    expect(md5Hex(text('a'.repeat(55)))).toBe('ef1772b6dff9a122358552954ad0df65');
    expect(md5Hex(text('a'.repeat(56)))).toBe('3b0c8ac703f828b04c6c197006d17218');
    expect(md5Hex(text('a'.repeat(64)))).toBe('014842d480b571495a4a0363793f7367');
  });
});
