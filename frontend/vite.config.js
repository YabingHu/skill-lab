import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时把 /api 代理到后端（默认 8600）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: { '/api': process.env.SKILL_LAB_API || 'http://127.0.0.1:8600' },
  },
  build: { chunkSizeWarningLimit: 2000 },
})
