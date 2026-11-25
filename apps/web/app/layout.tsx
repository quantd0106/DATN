import type { ReactNode } from 'react';
import type { Metadata } from 'next';
import { SessionProvider } from '@/components/session-provider';
import './globals.css';
import '../src/styles/lumi-foundation.css';
export const metadata: Metadata = {
  title: { default: 'Athena · Kết nối tri thức, vững bước tương lai', template: '%s · Athena' },
  description: 'Không gian học tập phổ thông Athena',
  icons: {
    icon: [
      { url: '/icon.svg', type: 'image/svg+xml' },
      { url: '/favicon.ico', sizes: '16x16' },
    ],
  },
};
export default function RootLayout({ children }: { children: ReactNode }) { return <html lang="vi"><body><a className="skip-link" href="#main-content">Đến nội dung chính</a><SessionProvider>{children}</SessionProvider></body></html>; }
