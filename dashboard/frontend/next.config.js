/** @type {import('next').NextConfig} */
const apiUrl = process.env.API_URL || 'http://localhost:8000'
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${apiUrl}/api/:path*`,
      },
      {
        source: '/cc/:path*',
        destination: `${apiUrl}/cc/:path*`,
      },
    ]
  },
}
module.exports = nextConfig
