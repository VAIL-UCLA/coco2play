import { defineConfig } from 'vite'

export default defineConfig({
  base: '/coco2play/',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
  },
  server: {
    fs: { allow: ['..'] }
  }
})
