# TSAgent Desktop — Design Spec (Blockade Skin)

Division-style HUD language: translucent glass plates float over a live field,
hairline seams separate surfaces, a single accent marks what has focus.
Source of truth: `src/design-system/tokens.css` + the `/* ══ Blockade skin pass ══ */`
block in `src/styles.css`. **Read this before adding any new page/component.**

## 1. Color

| Token | Value | Use |
|---|---|---|
| `--bg-root` | `#0d0e11` | page field (star map + workspaces) |
| `--bg-panel` | `rgba(16,17,20,.55)` | glass plate fill (translucent) |
| `--bg-panel-2` | `rgba(16,17,20,.78)` | inner/input surfaces |
| `--glass-edge` | `rgba(255,255,255,.10)` | top-edge light on plates |
| `--border-base` | `rgba(255,255,255,.08)` | hairline seams (default) |
| `--border-strong` | `rgba(255,255,255,.14)` | emphasized seams |
| `--text-main` | `#f2f4f6` | headings + primary text |
| `--text-muted` | `rgba(255,255,255,.55)` | secondary text |
| `--text-faint` | `rgba(255,255,255,.32)` | timestamps, hints |
| `--focus` | `#ff7f00` | amber focus accent (star-map chrome) |
| `--focus-deep` | `#d96600` | pressed/gradient end |
| `--focus-fill` | `rgba(255,127,0,.16)` | selection fill |
| `--focus-edge` | `rgba(255,127,0,.45)` | focus glow |
| `--planet-accent` | per-planet hex | identity marks ONLY (num chip, eyebrow, composer tag, corner brackets, `.on` bars) |

Rules:
- Chrome focus on the star map is amber. Inside a workspace, `--planet-accent`
  takes over the same roles (selection bar, focus edge, corner marks, eyebrows).
- Planet accent never paints large fills — it appears as lines, dots, small text.
- Status colors stay semantic: live/ok `#5fe0b1`, warn `#f2d06b`, danger `#ff6a4d`.
- No gradients as decoration; glass plates get `backdrop-filter: blur(14–16px)`
  + `inset 0 1px 0 var(--glass-edge)` + `0 8–10px 28–30px rgba(0,0,0,.45)` shadow.

## 2. Typography

| Role | Family | Size | Weight | Spacing |
|---|---|---|---|---|
| Display (ws-title, intel title) | `Borda`, Rajdhani fallback | 17–18px | 600 | .04em |
| Panel head (`mp-head h2`) | Rajdhani | 13px | 600 | .02em, sentence case |
| Status tags (`BLOCKED`, `R1`) | Rajdhani | 10–11px | 600 | .04em, uppercase |
| Body | `Rajdhani`, Titillium fallback | 12–13px | 400/500 | .02–.04em |
| Small / meta | `Rajdhani` | 10–11px | 400 | .04–.06em |
| Data / ids / paths | `monospace` | 10–11px | 400 | 0 |

- Headings: short, uppercase where they're labels; sentence case for content.
- Numbers that change use monospace/tabular figures.

## 3. Spacing

| Context | Value |
|---|---|
| Plate padding (`mp-body`) | 12–14px |
| Panel head padding | 10px 14px |
| Grid gaps between panels | 14px |
| Card internal gap | 8px |
| Page margins (`ws-canvas`) | 16px 20px |
| Row padding | 7–9px 9–11px |
| Composer bar | 10px 16px |

Breathing room comes from translucency + gaps, not padding inflation — keep
plates dense; the field behind shows through the glass.

## 4. Components

**Plate (`.mp`, `.solar-hud`, `.solar-intel`, `.chat-host`, `.settings-drawer`, `.tray-pop`)**
- `background: var(--bg-panel)` + `backdrop-filter: blur(14px)` + 1px `var(--border-base)`
  + `border-radius: 2px` + top-edge inset light + drop shadow
- Corner marks (`.mp::after`): two L-marks at TL + BR corners, `rgba(255,255,255,.3)`, 6px arms — neutral, never accent.
- Cut corner (existing `clip-path`) may stay on `.mp` for the mecha edge.

**Rows / list items**
- 1px transparent border + 2px transparent left bar; hover: `rgba(255,255,255,.05)` fill + translateY(-1px)
- Selected (`.on`): `rgba(255,255,255,.07)` fill + 2px `var(--planet-accent)` bar on the top edge (`inset 0 2px 0`)

**Buttons**
- `border-radius: 2px`, 1px `--border-base`, transparent fill
- Hover: border + text go `--planet-accent`, faint accent glow
- Primary action: accent border/text on rest; danger: `#ff6a4d` palette
- Never filled bright backgrounds for chrome buttons (exception: true CTAs like Resume)

**Inputs / textarea / select**
- `--bg-panel-2` fill, 1px `--border-base`, radius 2px
- `:focus` — border + 1px outline ring in `--planet-accent` + soft accent glow. No default browser outline.

**Navigation / chips / tabs**
- Scene chip, ws-num, ws-mode, tabs: 2px radius, hairline border, accent only on the active one
- Tabs: plain text, no boxes; active = main text color + 2px accent underline

**Scrollbar** — 8px, `rgba(255,255,255,.12)` thumb, `.22` hover.

## 5. Layout

- Star map `/`: full-bleed WebGL field; HUD blocks + intel + composer are floating
  glass plates — never side rails.
- Workspace `/w/:scene`: `ws-head` (num chip + Borda title + mode + back) →
  `ws-canvas` grid of glass `.mp` plates (2–3 columns per §6 layouts) →
  `ws-composer` bottom bar (`@planet` tag + input + send).
- Nothing renders opaque solid panels — every surface is glass over the field.
- **Pseudo-3D convergence**: the outer column of a workspace grid leans into the
  viewport — `.ws-grid > :first-child` gets `perspective(1200px) rotateY(-5deg)
  rotateX(0.6deg)` (origin left center), `:last-child` the mirror; the Sun rail
  and the star-map intel plate do the same. Center content stays flat.
- Empty states: `cap-empty` dashed hairline + accent dot + one-line guidance.

## 6. Restraint (what not to add)

- No eyebrow labels above panel titles; `MechaPanel` takes a `title` only.
- No ordinal chips (`01`, `04`) — scenes aren't a sequence. No build codes (`D4B1`, `V2.3D`) in UI.
- One brand mark (top bar). Workspace header = title + planet name + "System map".
- Panel titles and buttons are sentence case (`Recent runs`, `Open workspace`, `Save`);
  caps are reserved for status tags (`BLOCKED`, `R1`).
- Neutral grey is the default for every surface, border and corner mark. Accent appears
  only on focus: the selected row's top-edge bar, the active tab underline, a focused input edge.
- Star map shows nothing at rest except the scene, the starmap toggle and the composer;
  the intel plate appears only for a hovered/focused planet.
- Decorative textures (glyph ribbons, dot grids, scan rulers, slanted bevels) are not used.

## 7. Motion

- Interactions only: hover lift .12s, border/focus .15s, selection draw .15s.
- Ambient: `ws-breathe` field pulse 9s, scan/sweep effects already in the scene.
- `prefers-reduced-motion`: all ambient animation off.
