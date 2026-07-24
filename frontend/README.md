# Self-Healing Data Pipeline Platform — frontend

React + Vite + TypeScript + Tailwind CSS UI for the FastAPI backend, where LangGraph agents (with Google Gemini)
detect pipeline failures, diagnose them, try a fix, verify it, and either heal the run or escalate to a human.

## Run in development

Start the FastAPI backend on `http://127.0.0.1:8000`, then:

```bash
cd frontend
npm install
npm run dev
```

Open the URL Vite prints (usually http://localhost:5173). Requests to `/api` are proxied to the backend
(see `vite.config.ts`).

## Build

```bash
npm run build
```

The production files are written to `frontend/dist`. Serve them from the same origin as the API.

## Pages

- **Login** — shown when `GET /api/whoami` returns 401. The key is kept in `sessionStorage` and sent as
  `X-Access-Key` on every request; any 401 returns you here.
- **Run a test** (`#/run`) — pick a preset or upload a file, run pipeline A or B, and watch the agent flow replay
  the incident the run created.
- **Incidents** (`#/incidents`) — metrics, the incident timeline, a "Show only blocked fixes" filter, and the
  selected incident's audit trail, guard callout and technical details. Admins also see "Clear history".
- **Records** (`#/records`) — quarantined rows and escalations.

## Notes

- All API calls and response types are in `src/api.ts`.
- The footer links to FastAPI's interactive docs at `/docs`. In dev, `/docs` and `/openapi.json` are proxied to the
  backend along with `/api`.
- "Clear history" calls `POST /api/history/clear` (admin only). The route is set in `CLEAR_HISTORY` in `src/api.ts`.
- Data is always rendered as text (no `dangerouslySetInnerHTML`), so uploaded HTML or script text is shown, never run.
- Polling runs every 3 seconds and pauses while the browser tab is hidden.
- Theme follows the system setting until you use the toggle; the choice is saved in `localStorage`.
