# UX Contract

## Product context

- Audience: Operador individual de un flujo de noticias Fortnite.
- Primary jobs: Vigilar cuentas de X, reconocer publicaciones nuevas, abrir/copiar su enlace y enviarlas al flujo de Telegram cuando la integración esté conectada.
- Target market(s): Flujo personal en español, con escritorio como superficie principal y móvil como consulta rápida.
- Active locales: `es-ES` para UI y fechas; timezone del servidor verificado por el dashboard.
- Language/content register and native-review policy: Español directo, con verbos de acción; cualquier nuevo copy debe revisarse contra ese registro.
- Timezone/calendar policy: Mostrar tiempo relativo calculado con el reloj verificado del servicio; mostrar fecha absoluta como apoyo.
- Accessibility target: WCAG 2.2 AA

## Business-context sources

| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| Data lifecycle | `dashboard/service.py`, `dashboard/store.py` | API/domain code | 2026-10-01 |
| Deletion / retention | `dashboard/service.py::maintenance`, `dashboard/store.py::purge_older_than` | Domain code | 2026-10-01 |
| Telegram handoff | Pending Phase 2 adapter contract | Integration decision | 2026-10-01 |
| Product scope | User brief in task thread | Product brief | 2026-10-01 |

## Visual contract

- Project `DESIGN.md`: `DESIGN.md`
- Token ownership model: Existing runtime CSS is canonical; `DESIGN.md` mirrors the implemented values.
- Runtime design-system/token source: `dashboard/web/styles.css`
- Mapping/export/adapters: None; vanilla HTML/CSS/JS reads the CSS custom properties directly.
- Token drift gate: Run the premium `audit_project.py` and inspect the changed CSS/HTML before handoff.
- Supported themes: Dark graphite operational theme.
- Design-context owner/review policy: Keep the monitor rail, action hierarchy, and no-editor scope stable across future phases.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Select/Listbox | Native `<select>` | `dashboard/web/index.html` + CSS | native only | Keyboard and browser popup |
| Form | Manual form controller | `dashboard/web/app.js` | create account / confirm delete | Submit, invalid, retry |
| Scrollbar | Global CSS baseline | `dashboard/web/styles.css` | geometry exceptions only | Computed style / narrow viewport |
| Toast | `#toast` live-region stack | `dashboard/web/app.js` | success/warning/error | Live-region and failure-path check |
| CRUD accounts | Accounts tab + app dialog for delete | `/api/accounts*` | return to list | Full add/pause/delete flow |
| CRUD | Accounts tab + tweet mutations | `dashboard/web/app.js` + `/api/*` | return to current list | Full mutation flow |
| Tweet actions | Tweet card action row | `/api/tweets*` | open/copy/conserve; Telegram pending contract | Keyboard, disabled reason, API error |

## Component behavior

| Component | Default | Hover | Focus | Active | Disabled | Busy | Error |
|---|---|---|---|---|---|---|---|
| Button | Flat role-based surface | Brightness lift | Warm 2px ring | 1px press | Reduced opacity + reason | Stable width + spinner | Toast + retryable state |
| Input | Graphite field + line | Line brightens | Warm ring | n/a | Reduced opacity | n/a | Inline field message/toast |
| Search/filter | Committed on change | Same as input | Same | n/a | n/a | List loading state | Toast and preserved filter |
| Tweet card | Raised surface + signal edge | Border lift | Action focus | Action press | Action-specific explanation | Button spinner | Toast; card remains usable |

## Dataset navigation

- Admin tables: None; accounts are a compact operational list.
- Exploratory lists: Chronological tweet feed.
- URL state: Active tab in hash; filters are transient for now and reset only on a full reload.
- Page size: 48 by default, with explicit “Cargar más”.
- Empty/no-results/error/loading treatment: App-owned panel for each state, with a direct next step.
- Back/scroll restoration: Hash navigation preserves the browser page; loading more appends without replacing the current list.
- Selection scope: No bulk selection in Phase 1.

## Flow ledger

| Operation | Trigger | Pending | Success destination | Success feedback | Failure recovery | Focus outcome | Source ref |
|---|---|---|---|---|---|---|---|
| Search | “Buscar ahora” | Poller rail + busy button | Bandeja | New-count toast | Keep existing feed and show error | Return to scan button |
| Copy | “Copiar enlace” | Button spinner | Same card | Toast | Inline copy fallback | Button keeps focus |
| Conserve | “Conservar” | Button spinner | Same feed/filter | Toast | Card remains | Button keeps focus |
| Delete account | “Eliminar” then dialog | Dialog busy state | Accounts list | Toast | Dialog stays open with error | Focus returns to row action |
| Telegram handoff | Phase 2 contract | Queued delivery state | Same card / Telegram confirmation | Delivery result | Retry without duplicate submit | Focus returns to action |

## Navigation and responsive behavior

- Route document title policy: `EditImg · Radar`, `EditImg · Cuentas`, `EditImg · Sistema`.
- Route error / 403 page behavior: Preserve the server response and use Spanish recovery copy; no silent retry loop.
- Breadcrumb/tab/route-state policy: Three hash-addressable tabs; no editor route.
- Sidebar/drawer/bottom-sheet transformation: None in Phase 1.
- Responsive table strategy: Account rows wrap into stacked actions; tweet actions stack full width under 700px.
- Truncation/full-value access: Tweet body wraps; URLs are copied/opened rather than visually truncated as the only access.
- Focus restoration and sticky-obstruction policy: Focus remains on the initiating action after mutation; sticky header never hides focused content.

## Overlays and feedback

- Dialog primitive: Native `<dialog>` styled as the app-owned confirmation primitive.
- Destructive confirmation levels: Account removal requires explicit confirmation; retention purge is manual and labeled with its consequence.
- Toast placement/duration/deduplication: Bottom-right desktop, bottom inset mobile; 4.2s success/info, 9s error; one message per mutation.
- Alert/banner scope and persistence: Monitor and Telegram status remain visible in the rail; errors are transient plus event log.
- Tooltip delay/dismissal: Native title only for short supplementary disabled reasons; action labels remain visible.
- Layer/z-index contract: Dialog > toast > sticky header > page content.

## Async and resilience

- Mutation default: Pessimistic; the feed changes after the API succeeds.
- Idempotency and duplicate-submit policy: Disable initiating button while awaiting; poller rejects concurrent runs.
- Offline/read-stale/write behavior: Preserve rendered feed; show a toast on failed refresh and keep the last known monitor state.
- Retry/backoff/timeout behavior: Browser refresh calls are bounded by the server request; the manual poll can continue in the background and is observable from the rail.
- Long-running progress and return path: Progress stays in the monitor rail; user may switch tabs without losing the run.
- Stale-request cancellation/invalidation: Increment the list request generation; stale responses do not overwrite newer filters.

## Validation

- Schema/validation layer: Server-side `DashboardError` plus client-side required account input.
- Trigger timing: Account validation on submit; filters commit on change.
- Error summary/inline policy: Toast for API failures; field remains populated for correction.
- Sensitive-value handling: Tokens never enter HTML or toast content; only configured/not-configured booleans are exposed.
- Duplicate-submit prevention: Busy state on all mutating controls.

## Permission and clipboard

- Permission UI strategy: Telegram action is disabled with an explanation until its adapter is configured; no misleading success state.
- Clipboard copy policy: Copy the full URL; success toast contains no secret and failure exposes a visible copyable link.
- Disabled-state explanation: `title` plus adjacent status copy for unavailable Telegram.

## Migration status

- Migration ledger location: This document and `DESIGN.md`.
- Canonical primitives and owners: Native fields, `#toast`, app-owned dialog, tweet card action row, monitor rail.
- Current risk-prioritized slices: Phase 1 visual/interaction shell; Phase 2 Telegram adapter contract; Phase 3 real browser/device verification and cleanup.
- Legacy import/token enforcement: Do not reintroduce editor routes or legacy card actions into the dashboard.
- Rollout/rollback and removal gates: Keep backend API compatibility; UI can roll back to the current read-only endpoints without data migration.

## Verification

- Required static commands: Project tests, frontend premium `audit_project.py --mode strict`.
- Browser/device/locale/theme matrix: Chromium desktop 1440px, narrow mobile viewport, Spanish locale, reduced motion spot-check.
- Accessibility checks: Keyboard tab order, visible focus, dialog focus return, live-region messages, native select operation.
- Component-state/visual regression coverage: Loading, empty, no-results, error, busy poller, Telegram unavailable, account delete dialog.
- Project audit command/result: Run after Phase 1 edits; record blockers in the handoff.
- CRUD full-flow evidence: Add, pause/activate, confirm delete account; no unconfirmed destructive request.
- Failure-path evidence: Failed API, failed clipboard, busy poll conflict, and absent Telegram configuration.
