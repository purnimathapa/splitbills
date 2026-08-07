# SplitBills Design System

Foundation for a polished financial UI. Built on plain HTML/CSS/JS — no frontend framework.

## File structure

| File | Purpose |
|------|---------|
| `css/tokens.css` | Colors, typography scale, spacing, radius, shadows |
| `css/components.css` | Reusable components (buttons, forms, cards, tables, etc.) |
| `css/theme.css` | Page layouts, charts, feature-specific overrides |
| `css/sw-app.css` | Splitwise-style shell (sidebar, expense rows, modals) |

Load order in `base.html`:

```html
<link rel="stylesheet" href="…/tokens.css">
<link rel="stylesheet" href="…/components.css">
<link rel="stylesheet" href="…/theme.css">
<link rel="stylesheet" href="…/sw-app.css">
```

## Color palette

- **Primary** — deep teal (`#0f5c52`): actions, links, active nav
- **Success / receivable** — `#047857`: money owed to you, paid status
- **Danger / owed** — `#b42318`: amounts you owe
- **Warning** — `#b45309`: claims pending, review needed
- **Neutral** — slate grays for text and borders

Semantic tokens: `--color-owed`, `--color-receivable`, `--color-paid`, `--color-settled`

## Typography

| Class | Use |
|-------|-----|
| `.type-display` | Hero numbers, landing headlines |
| `.type-h1` / `h1` | Page titles |
| `.type-h2` | Section headings |
| `.type-h3` | Card section titles |
| `.type-body` | Default body copy |
| `.type-caption` / `.text-caption` | Secondary metadata |
| `.type-label` / `.form-label` | Form field labels |
| `.type-overline` / `.text-upper` | Eyebrow labels |

## Financial amounts

Always use tabular figures for money:

```html
<span class="amount amount--lg amount--receivable tabular-nums">Rs 1,250.00</span>
```

| Class | Meaning |
|-------|---------|
| `.amount--lg` | Large balance display |
| `.amount--sm` | Inline list amounts |
| `.amount--owed` / `.text-owed` | You owe (red) |
| `.amount--receivable` / `.text-receivable` | Owed to you (green) |
| `.amount--paid` / `.text-paid` | Payment confirmed |
| `.amount--neutral` / `.text-settled` | Zero balance |
| `.tabular-nums` | Monospaced digits (use on all currency) |

Balance cards (`.balance-card--positive/negative/neutral`) use a left border + soft tint — no gradients.

## Buttons

| Class | Use |
|-------|-----|
| `.btn-primary` | Primary action |
| `.btn-secondary` | Secondary / cancel |
| `.btn-sm` | Compact inline actions |
| `.btn-block` | Full-width |
| `.btn-ghost` | Tertiary text button |

## Forms

```html
<label class="form-label" for="amount">Amount</label>
<input class="input-field input-field--amount" id="amount" type="number" step="0.01">
<p class="input-hint">Split equally among 4 people</p>
<p class="input-error">Amount must be greater than zero</p>
```

- `.input-field` — text, number, file, textarea
- `select.input-field` — styled select with chevron
- `.form-stack` — vertical form layout
- `.form-row-inline` — checkbox rows

## Cards & stats

| Class | Use |
|-------|-----|
| `.card` | Default content container |
| `.card--flush` | List inside card (no padding) |
| `.card--muted` | Subtle background |
| `.stat-card` | Metric tile |
| `.stat-card--accent` | Highlighted metric |

## Tables

Use `.data-table` for structured financial data:

```html
<table class="data-table">
  <thead><tr><th>Person</th><th class="amount">Amount</th></tr></thead>
  <tbody><tr><td>Alice</td><td class="amount amount--owed">Rs 500.00</td></tr></tbody>
</table>
```

List rows (`.list-row`, `.sw-expense-row`) inherit tabular nums on amount columns.

## Badges

`.badge-success` · `.badge-danger` · `.badge-warning` · `.badge-neutral` · `.badge-info`

## Alerts

```html
<div class="alert alert--error">
  <p class="alert__title">Payment failed</p>
  <p>Could not confirm Khalti transaction.</p>
</div>
```

Variants: `.alert--success`, `.alert--error`, `.alert--warning`, `.alert--info`

## Navigation

- **Top bar** — `.app-nav`, `.app-nav__brand`, `.app-nav__actions`
- **Sidebar** — `.sw-sidebar`, `.sw-sidebar__link.is-active`
- **Mobile tabs** — `.tab-bar`, `.tab-bar__item.is-active`
- **Segmented tabs** — `.tabs` / `.tabs__tab` (also `.analytics-range`)

## Dropdowns

`.dropdown` > `.dropdown__menu` > `.dropdown__item`

Notification panel (`.notif-panel`) shares dropdown surface styles.

## Empty states

```html
<div class="empty-state empty-state--rich">
  <span class="empty-state__icon" aria-hidden="true">…</span>
  <h3 class="empty-state__title">No expenses yet</h3>
  <p class="empty-state__sub">Add your first bill to get started.</p>
</div>
```

## Modals

- **Center modal** — `.sw-modal`, `.sw-modal__dialog`
- **Bottom sheet** — `.modal-sheet`, `.modal-sheet__panel`

## Dark mode

Toggle via `[data-theme="dark"]` on `<html>`. All tokens redefined in `tokens.css`.

## Migration notes

Existing template class names are preserved. New pages should prefer semantic amount classes (`.amount--owed`, etc.) over generic `.text-danger` where the meaning is financial.

Page-by-page migration is intentionally deferred — the foundation applies globally via tokens and shared components.
