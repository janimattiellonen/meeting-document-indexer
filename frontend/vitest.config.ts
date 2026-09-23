import { defineConfig } from "vitest/config";

// Separate from vite.config.ts: the React Router plugin isn't needed (or wanted) for unit tests.
export default defineConfig({
  resolve: {
    tsconfigPaths: true,
  },
  test: {
    environment: "jsdom",
    include: ["app/**/*.test.{ts,tsx}"],
  },
});
