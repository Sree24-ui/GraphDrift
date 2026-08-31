import path from 'node:path'
import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

const here = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = env.VITE_DEV_BACKEND_URL?.trim()

  if (command === 'serve' && mode === 'development' && !backendTarget) {
    console.warn(`
⚠️  VITE_DEV_BACKEND_URL is not set — API requests will fail with 404.
    Run: cp .env.development.example .env.development
    Then restart the dev server and verify the backend URL in that file.
`)
  }

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@shared': path.resolve(here, '../shared'),
      },
    },
    server: {
      proxy: backendTarget
        ? {
            '/api': backendTarget,
            '/health': backendTarget,
            '/ws': {
              target: backendTarget.replace(/^http/, 'ws'),
              ws: true,
            },
          }
        : undefined,
    },
  }
})
