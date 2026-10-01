import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'ConsMan | The Blessing Edu', description: 'Education consultancy CRM and student relationship management' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
