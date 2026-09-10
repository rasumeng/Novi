/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  build: {
    chunkSizeWarningLimit: 850,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules/react') || id.includes('node_modules/react-dom')) return 'react'
          if (id.includes('node_modules/katex') || id.includes('react-markdown') || id.includes('rehype-katex') || id.includes('remark-')) return 'katex'
          if (id.includes('node_modules/lucide-react')) return 'lucide'
          if (id.includes('node_modules/@tauri-apps')) return 'tauri'
          if (id.includes('node_modules/framer-motion')) return 'motion'
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
})
