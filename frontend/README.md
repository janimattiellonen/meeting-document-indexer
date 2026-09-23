# Frontend

React Router (framework mode, SPA: `ssr: false`) + TypeScript + Tailwind. Finnish UI for searching the
meeting minutes indexed by the backend.

```bash
pnpm install
pnpm dev          # http://127.0.0.1:5180, proxies /api to the backend (`uv run mi serve`)
pnpm test         # Vitest
pnpm typecheck
pnpm gen:api      # regenerate app/api/schema.d.ts from the backend's OpenAPI schema
```

Everything stays on this machine: the dev server listens on 127.0.0.1 only, and the app loads no web
fonts or other external resources.
