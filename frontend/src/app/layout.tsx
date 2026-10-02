import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  title: { default: 'ConsMan', template: '%s | ConsMan' },
  description: 'Education consultancy CRM and student relationship management',
  icons: { icon: { url: '/logo-consman.jpg', type: 'image/jpeg' }, apple: '/logo-consman.jpg' },
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
