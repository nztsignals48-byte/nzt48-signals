import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'NZT-48 Command Center',
  description: 'Trading Signal Engine Dashboard v9.0',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.className} bg-nzt-bg text-nzt-text min-h-screen`}>
        {children}
      </body>
    </html>
  )
}
