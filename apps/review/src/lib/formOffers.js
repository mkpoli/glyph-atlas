// Which registered figures and regional shapes a crop's form bar offers beside the character itself.
// The site's faces draw a bare character with Noto CJK JP glyphs (docs/typefaces.md), so two of them
// draw what the character's own chip already shows: the IVD figure a font gives the character alone
// (邦 U+90A6 U+E0103, MJ026242), and the regional description that holds in Japan (邦's ⿰丰阝 of
// GHTJKV). Neither is offered again. A crop already filed under one keeps it on its bar.

/** The registered figures that draw unlike the bare character: all but the default one. */
export const offeredFigures = figures => figures.filter(figure => !figure.default)

/** The regional shapes that draw unlike the bare character: all but the one whose regions include J. */
export const offeredShapes = shapes => shapes.filter(shape => !shape.regions.includes('J'))
