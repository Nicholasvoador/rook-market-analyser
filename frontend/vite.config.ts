import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: `npm run dev` on :5173 proxies to the backend on :8787. Prod: backend serves dist/.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8787',
      '/ws': { target: 'ws://127.0.0.1:8787', ws: true },
    },
  },
  build: { chunkSizeWarningLimit: 900 },
})
