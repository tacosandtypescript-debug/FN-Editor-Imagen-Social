---
version: alpha
name: "EditImg Radar"
description: "Un radar operativo oscuro y preciso para descubrir publicaciones de X y encaminarlas a Telegram."
colors:
  background: "#080b10"
  surface: "#111821"
  surfaceRaised: "#18222c"
  surfaceBright: "#202c38"
  primary: "#62e6ff"
  primaryStrong: "#1ec9ec"
  telegram: "#4ac5c8"
  attention: "#f7b955"
  success: "#6fe0a1"
  danger: "#ff7384"
  text: "#f6f8fb"
  muted: "#91a0ad"
  line: "#25313e"
  focus: "#f7d774"
typography:
  sans:
    fontFamily: "Segoe UI Variable, Segoe UI, system-ui, sans-serif"
  mono:
    fontFamily: "Cascadia Code, Consolas, ui-monospace, monospace"
rounded:
  DEFAULT: "0.875rem"
  sm: "0.625rem"
  md: "0.875rem"
  lg: "1.25rem"
spacing:
  section-gap: "1.5rem"
  page-max: "74rem"
components:
  button: { }
  card: { }
  dialog: { }
  input: { }
  status-rail: { }
  tweet-card: { }
  toast: { }
---

# EditImg Radar Design System

## Overview

### Creative North Star

EditImg Radar behaves like a small control room: a dark graphite surface, a
single electric signal line, and calm operational readouts. The interface
should feel like a live radar for Fortnite/X signals, not like a media editor
or a generic admin template.

### Product context and register

- **Audience and primary job:** One operator monitoring Fortnite accounts, spotting new posts, copying a source link, and later routing a selected post into a Telegram workflow.
- **Target market(s) and evidence:** Spanish-speaking creator workflow; the repository UI and source documentation are Spanish and the user explicitly described a personal monitoring tool.
- **Locale(s) and language policy:** Spanish UI and content. Dates use the browser's Spanish locale with the verified server clock. Technical identifiers remain readable in monospace when shown in Sistema.
- **Usage scene:** Frequent desktop use with occasional mobile checks; the first viewport must expose monitor health, new-item count, and the primary scan action.
- **Register:** Operational product UI with a restrained Fortnite signal accent. It is not an editor surface.
- **Memorable signature:** The monitor rail: a thin cyan signal edge plus four compact readouts for monitor, new posts, next scan, and Telegram readiness.
- **Restraint:** No decorative gradients, no editor controls, no equal-weight pill cloud, no full-screen marketing hero.
- **Anti-references:** The retired multi-tab editor, dense purple/orange card composer, and generic rounded SaaS dashboards that hide status behind decorative cards.
- **Token ownership/runtime mapping:** `dashboard/web/styles.css` is the runtime canonical source. This file mirrors its concrete tokens; changes to runtime tokens must be reflected here and checked by the premium frontend audit.

## Colors

The base is near-black graphite rather than pure black so media and borders have
room to separate. Cyan is reserved for discovery and primary navigation; teal
is reserved for Telegram delivery; amber means attention or a manual action;
green means a stable/retained state; red means a failure or destructive action.
Text hierarchy is white, muted blue-grey, and a faint line token. Focus uses a
warm high-contrast ring and is never removed. There is currently one dark theme;
the semantic roles are named so a future light theme can be added without
changing component vocabulary.

## Typography

`Segoe UI Variable` is preferred because this is a Windows-first tool, with
`Segoe UI` and system fallbacks. Headings are compact, sentence case, and use
weight 700/800. Body copy stays at a readable 14–16px measure. Technical
timestamps, IDs, and diagnostics use the mono stack. All user-facing copy is
Spanish; actions use direct verbs: “Buscar ahora”, “Copiar enlace”, “Abrir en
X”, “Conservar”.

## Layout

The page uses a centered max width of 74rem with a 16–24px gutter. A sticky
header contains identity and the one primary scan action. The first screen is
organized as: hero/monitor rail, filters, chronological feed. The feed remains
a single vertical reading order; media sits inside the card instead of creating
a separate gallery. At 700px the header becomes static, controls wrap, and
tweet actions become thumb-sized full-width rows. Safe-area padding is applied
on mobile.

## Elevation & Depth

Hierarchy comes from three flat tonal surfaces and one-pixel borders. Shadows
are reserved for the confirmation dialog and toast stack. Sticky surfaces use a
solid translucent graphite with a border; there are no large blurred color
fields behind content.

## Shapes

Containers use a medium 14px radius. Controls use 9–10px. Status tokens are
compact capsules only when they communicate state; navigation is an underline
and signal edge, not a row of pills. Dividers are one-pixel lines with no
decorative ornaments.

## Components

### Foundational visual states

Every interactive control has visible hover, focus-visible, pressed, disabled,
and busy states. Loading uses an inline spinner with stable button geometry.
Empty, no-results, error, and stale-monitor states are app-owned panels with a
next action. Reduced-motion users receive no animated transition.

### Buttons and actions

Primary: cyan, used for “Buscar ahora”. Telegram: teal, used only for delivery.
Secondary: flat raised surface, used for copy/open/filters. Tertiary: ghost,
used for conservation or navigation. Destructive: red outline and always
requires the app-owned confirmation dialog. Labels remain visible; icons never
carry the full meaning alone.

### Navigation and data display

The three routes are Bandeja, Cuentas, and Sistema. Tabs are URL-hash addressable
and expose `aria-selected`/`aria-controls`. Publications are chronological
cards grouped by day, with the newest first. Counts are concise operational
readouts, not a dump of internal legacy statuses.

### Forms and overlays

Native select/input controls remain the canonical form controls. Deletion uses
an app-owned dialog, not `window.confirm`; clipboard failure uses a toast and a
visible inline link, not `window.prompt`. Toasts are live regions and do not
contain secrets.

### Iconography

The first pass uses small text labels and restrained Unicode/SVG-free markers
so labels remain available on every device. A future icon set must be a single
stroke family and cannot replace the action text on destructive or delivery
actions.

### Motion

Motion is feedback only: 140–180ms control transitions, a short toast entrance,
and a spinner while a poll is running. Long-running polling reports progress in
the monitor rail. `prefers-reduced-motion` disables nonessential movement.

### Content and data visualization

Product language is calm and explicit. “Nuevas” means posts not yet attended;
“Conservadas” means posts protected from retention cleanup. The UI never exposes
internal editor statuses such as `tarjeta_lista` in the primary surface.

## Do's and Don'ts

- **Do:** Make monitor health, new posts, and the next scan visible before the feed.
- **Do:** Keep every publication action close to its source post.
- **Don't:** Reintroduce editor, video-editing, composition, or analysis controls into the dashboard.
- **Don't:** Use gradients or a wall of pills to manufacture hierarchy.
