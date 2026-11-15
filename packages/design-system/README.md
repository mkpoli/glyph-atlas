# Glyph Atlas design system

Tokens, element defaults and the controls that Glyph Atlas surfaces share. The review app's
`app.css` still holds the view-specific rules; the palette and the primitives here come from it, so
a page and its documentation resolve the same colours in light and dark.

## Use

```css
@import '@glyph-atlas/design-system/tokens.css';
```

`index.css` adds element defaults and the `ga-` components. The review app imports `tokens.css`
only, because it already has its own element rules; a new app can import `index.css`.

## Files

| File | Contents |
| --- | --- |
| `src/tokens.css` | Colour pairs, type scale, spacing, radii, control heights, fonts. |
| `src/base.css` | Resets, body type, links, buttons, inputs, focus ring, scrollbars. |
| `src/components.css` | `ga-button`, `ga-field`, `ga-input`, `ga-select`, `ga-textarea`, `ga-choice`, `ga-pill`, `ga-badge`, `ga-tabs`, `ga-card`, `ga-toolbar`, `ga-menu`, `ga-empty`. |

## Rules

- One primary action per view; `ga-button--primary` is the solid accent button and everything else
  is a surface or a quiet link.
- State is not colour alone. A checked `ga-pill` fills its checkbox and draws a check; a status
  badge carries its word, not only its hue.
- Focus is the 3px `--focus-ring` outline with a 2–3px offset. Do not remove it; style the control
  around it.
- Controls are at least 40px high (44px for the primary action) and inputs use `--line` at rest,
  `--line-strong` on hover.
- Keep the palette in this package. Change a token here once, and the review app, a new app and the
  design page follow.

## Design page

The review app serves it at `/design` (redirected to the reader's locale): foundations, components and patterns in both schemes, with
the exact class names above.
