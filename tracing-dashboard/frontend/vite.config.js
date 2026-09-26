import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 构建产物输出到 dist/，由多阶段 Dockerfile 交给 nginx 托管
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      // 本地开发时把 API/SSE 代理到后端容器
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
