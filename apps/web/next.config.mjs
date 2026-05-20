/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Produces a self-contained server bundle in .next/standalone so the
  // production Docker image only carries the files actually needed at
  // runtime — much smaller than copying node_modules wholesale.
  output: "standalone",
};

export default nextConfig;
