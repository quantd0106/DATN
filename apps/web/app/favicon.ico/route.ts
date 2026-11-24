import { NextResponse } from 'next/server';

const ICO_BYTES = (() => {
  const header = [
    0x00, 0x00, 0x01, 0x00, 0x01, 0x00,
    16, 16, 0, 0, 1, 0, 32, 0,
    0x68, 0x04, 0x00, 0x00,
    22, 0, 0, 0,
  ];
  const dibHeader = [
    40, 0, 0, 0,
    16, 0, 0, 0,
    32, 0, 0, 0,
    1, 0, 32, 0,
    0, 0, 0, 0,
    0x00, 0x04, 0x00, 0x00,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
  ];
  const bytes = new Uint8Array(header.length + dibHeader.length + 1024 + 64);
  bytes.set(header, 0);
  bytes.set(dibHeader, header.length);
  let offset = header.length + dibHeader.length;
  for (let y = 0; y < 16; y += 1) {
    for (let x = 0; x < 16; x += 1) {
      const inBook = x >= 3 && x <= 12 && y >= 4 && y <= 11 && x !== 8;
      bytes[offset++] = inBook ? 255 : 235;
      bytes[offset++] = inBook ? 255 : 99;
      bytes[offset++] = inBook ? 255 : 37;
      bytes[offset++] = 255;
    }
  }
  return bytes;
})();

export function GET() {
  return new NextResponse(ICO_BYTES, {
    status: 200,
    headers: {
      'Content-Type': 'image/x-icon',
      'Cache-Control': 'public, max-age=86400',
    },
  });
}
