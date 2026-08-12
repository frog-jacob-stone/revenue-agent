import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Vite inlines `import.meta.env.VITE_*` at build time — there is no runtime
// configuration to fix afterwards. A bundle built without these is broken the
// moment it is served, and the failure looks like a backend outage rather than a
// build mistake, so it is worth catching here where the output is a red
// pipeline instead of a white screen.
const REQUIRED = ['VITE_API_URL', 'VITE_SUPABASE_URL', 'VITE_SUPABASE_PUBLISHABLE_KEY']

export default defineConfig(({ mode }) => {
  // loadEnv merges prefixed entries from process.env, so CI supplies these
  // through the workflow's `env:` block with no .env file on disk.
  const env = loadEnv(mode, process.cwd(), 'VITE_')

  if (mode === 'production') {
    const missing = REQUIRED.filter((key) => !env[key])
    if (missing.length) {
      throw new Error(`Production build is missing: ${missing.join(', ')}`)
    }
    // A left-over local value is worse than a missing one: the build succeeds
    // and the bundle points at the developer's laptop.
    for (const key of ['VITE_API_URL', 'VITE_SUPABASE_URL']) {
      if (/localhost|127\.0\.0\.1/.test(env[key])) {
        throw new Error(`${key} points at localhost in a production build`)
      }
    }
  }

  return {
    plugins: [react()],
    server: {
      host: true,
      port: Number(process.env.PORT) || 3000,
    },
  }
})
