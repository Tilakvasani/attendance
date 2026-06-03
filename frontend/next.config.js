/** @type {import('next').NextConfig} */
const nextConfig = {
  // All /api/* requests in the frontend are proxied to FastAPI on port 8000.
  // This avoids CORS issues and means the frontend never hard-codes the backend URL.
  async rewrites() {
    return [
      {
        source:      "/api/:path*",
        destination: "http://localhost:8000/:path*",
      },
    ];
  },
};

module.exports = nextConfig;
