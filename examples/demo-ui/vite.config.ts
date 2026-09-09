import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: { port: 5180 },
  // Railway serves on a generated *.up.railway.app domain, which vite preview
  // rejects as an unknown host unless it is allowed explicitly.
  preview: { port: Number(process.env.PORT) || 4173, allowedHosts: true },
})
