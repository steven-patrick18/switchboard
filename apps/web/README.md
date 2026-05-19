# apps/web

Next.js 14 (App Router) operator dashboard — client workspaces, approval queue,
mobile-responsive.

Stack: React 18 · TypeScript · TailwindCSS · (shadcn/ui added later)

## Local development

1. Configure the API base URL:
   ```
   cp .env.local.example .env.local
   ```
2. Install deps and run:
   ```
   npm install
   npm run dev
   ```
3. Open http://localhost:3000 — register an operator, then manage client
   workspaces. Requires the API (`apps/api`) running on the URL in
   `.env.local`.

Pages: `/login` (register / sign in), `/workspaces` (list + create clients).
Auth token is stored in `localStorage` and sent as a Bearer header by
`lib/api.ts`.
