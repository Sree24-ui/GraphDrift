import path from 'node:path'
import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

const here = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = env.VITE_DEV_BACKEND_URL

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
