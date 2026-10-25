# YMVA Design System

Derived from yellowmetal.co (binding brand commitment, see PRODUCT.md). Operate-mode product UI: restrained, familiar, precise. Tokens live in `web/src/app/globals.css`.

## Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--ink` | `#0a0a0a` | `#f2f2f2` | Primary text, primary button fill |
| `--paper` | `#ffffff` | `#111111` | Content surface |
| `--rail` | `#f4f4f5` | `#161616` | Sidebar / secondary neutral layer |
| `--surface` | `#f0f0f0` | `#1f1f1f` | Inputs, table header, subtle fills |
| `--line` | `rgba(0,0,0,.09)` | `rgba(255,255,255,.1)` | Borders, dividers |
| `--muted` | `rgba(0,0,0,.58)` | `rgba(255,255,255,.62)` | Secondary text (≥4.5:1) |
| `--gold` | `#f5c542` | `#f5c542` | Accent: current selection, focus ring, primary chart series. Never decoration, never text on white. |
| `--success` / `--warning` / `--danger` | muted green / amber / red | | State only |

## Type

- **Inter** for all UI text, labels, buttons and data (tabular numerals for numbers).
- **Bricolage Grotesque 700–800** only for page titles (h1), tight tracking (-0.02em).
- Fixed rem scale, ratio ≈1.2: 12 / 13 / 14 (body) / 16 / 20 / 24 / 28.

## Shape & space

- 4 px base grid; page padding 32 px desktop, 16 px phone.
- Buttons, chips, segmented controls: pill (9999 px). Panels, tables, inputs: 12 px radius.
- Borders over shadows; one soft shadow only for floating layers (menus, toasts).

## Components

- Primary action: black pill button (gold focus ring). One per screen.
- Secondary: outlined pill. Destructive: danger text button + confirm.
- Every component ships hover, focus-visible, active, disabled, loading, error.
- Loading = skeletons; empty states explain what will appear and how to make it appear.
- Icons: lucide, 1.75 stroke, 16–18 px.

## Layout

- Left sidebar (240 px) with Overview, Agents, Calls, Campaigns, Tools, Numbers, Integrations, Settings; collapses to a top bar + drawer under 900 px.
- Content max width 1200 px; tables full width.

## Motion

150–200 ms ease-out for state changes only. Respect `prefers-reduced-motion`.
