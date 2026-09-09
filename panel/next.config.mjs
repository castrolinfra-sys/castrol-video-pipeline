/** @type {import('next').NextConfig} */
const nextConfig = {
  // Every page in this app reads live pipeline state. A cached render of a
  // jobs table is worse than useless - it is a stale answer to "what is
  // running right now" that looks authoritative.
  experimental: { staleTimes: { dynamic: 0, static: 0 } },
};
export default nextConfig;
