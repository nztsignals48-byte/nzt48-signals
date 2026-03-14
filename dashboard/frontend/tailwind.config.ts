import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        nzt: {
          bg: '#0a0a0f',
          card: '#12121a',
          border: '#1e1e2e',
          accent: '#00ff88',
          danger: '#ff4444',
          warning: '#ffaa00',
          text: '#e0e0e0',
          muted: '#6b7280',
        },
      },
    },
  },
  plugins: [],
}
export default config
