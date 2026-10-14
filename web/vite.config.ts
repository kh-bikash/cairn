import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // SSE needs the proxy to pass chunks through unbuffered; Vite's http-proxy does by default.
      '/api': { target: process.env.CAIRN_API ?? 'http://localhost:8000', changeOrigin: true },
    },
  },
})
