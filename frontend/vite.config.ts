import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Fixed, dedicated port so this never collides with other local dev servers.
  server: {
    port: 5180,
    strictPort: true,
  },
})
