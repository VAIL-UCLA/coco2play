import { defineConfig } from 'vite'
import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..')

// Serve `data/` and `urdf/` from the repo root during `npm run dev`.
// In production these are copied into `dist/` by the GitHub Actions workflow
// (see .github/workflows/deploy.yml), so no runtime indirection is needed.
function serveRepoAssets() {
  return {
    name: 'serve-repo-assets',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        // Tolerate both bare `/data/...` and base-prefixed `/coco2play/data/...`.
        const match = req.url && req.url.match(/^(?:\/coco2play)?\/(data|urdf)\/([^?#]+)/)
        if (!match) return next()
        const filePath = path.join(repoRoot, match[1], decodeURIComponent(match[2]))
        // Stay inside the repo root.
        if (!filePath.startsWith(repoRoot + path.sep)) return next()
        fs.stat(filePath, (err, stat) => {
          if (err || !stat.isFile()) return next()
          fs.createReadStream(filePath).pipe(res)
        })
      })
    },
  }
}

export default defineConfig(({ command }) => ({
  // Pages serves the site under /coco2play/, but locally we want plain /.
  base: command === 'build' ? '/coco2play/' : '/',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
  },
  server: {
    fs: { allow: ['..'] },
  },
  plugins: [serveRepoAssets()],
}))
