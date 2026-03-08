import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const vitePort = parseInt(env.VITE_PORT || '3000', 10)
  const flaskPort = parseInt(env.FLASK_PORT || '5555', 10)

  return {
    plugins: [react()],
    server: {
      port: vitePort,
      proxy: {
        // Forward all /api calls to the Flask backend during development
        '/api': {
          target: `http://localhost:${flaskPort}`,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
    },
  }
})
