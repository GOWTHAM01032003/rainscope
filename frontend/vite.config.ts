import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', ['RAINSCOPE_'])
  const apiTarget = env.RAINSCOPE_API_PROXY_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [react(), {
      name: 'rainscope-api-target',
      configureServer(server) {
        server.config.logger.info(`[RainScope] /api proxy target: ${apiTarget}`)
      },
    }],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy: { '/api': { target: apiTarget, changeOrigin: true } },
    },
  }
})
