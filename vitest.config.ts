import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const contractsSrc = fileURLToPath(
  new URL('./packages/contracts/src/index.ts', import.meta.url),
);

export default defineConfig({
  test: {
    include: ['packages/**/tests/**/*.test.ts'],
    environment: 'node',
    globals: false,
  },
  resolve: {
    alias: {
      '@hermes-ops/contracts': contractsSrc,
    },
  },
});
