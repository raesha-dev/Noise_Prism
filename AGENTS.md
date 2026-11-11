<!-- LOVABLE:BEGIN -->

> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.

## What Lovable manages

Rules outside this block are the project's own. Change the pieces below in
Lovable, not by hand. With the [Lovable MCP](https://mcp.lovable.dev)
connected, `send_message` asks Lovable to do it; otherwise ask the user to
use the editor.

- **Database.** Do not add or edit `supabase/migrations/` or `drizzle/`.
  Ask Lovable to change the schema.
- **Generated files.** Do not edit a file whose header says it is automatically
  generated.
- **AI gateway.** Keep model calls on Lovable's gateway, and keep
  `LOVABLE_API_KEY` out of browser code.
- **Connectors.** Lovable provides native connectors. `list_connectors` shows
  which are enabled; `add_connector` only returns the dashboard link. When a
  connector is enabled, calls go through the connector gateway and need
  `LOVABLE_API_KEY`. When Lovable does not provide one, users can bring their
  own key and store it in Secrets.
- **Secrets.** Lovable manages them. Do not put secret values in the repo.

<!-- LOVABLE:END -->

## Project rules

- The browser talks only to a user-run FastAPI/Flask proxy via `src/lib/bharatnoise-api.ts`; the wire contract lives in `docs/proxy-contract.md` and both must change together. Why: Gnani credentials must never reach the browser.
- Live-monitor framing constants live in `src/lib/pcm-framer.ts` and are covered by tests. Why: the 8 kHz / 1,024-byte / 64 ms contract is fixed by the upstream ASR.
- Every metric shown in the UI carries a source tag (service, browser estimate, illustrative, imported). Why: sample numbers must never read as verified results.
- Workstation UI is split into `src/components/workstation/*`; `Workstation.tsx` owns state, panels are presentational. Why: keeps the single route manageable.
