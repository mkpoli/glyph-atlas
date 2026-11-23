// The builds of Atlas Sans and Atlas Serif the site serves (`typefaces.ts`), newest first. A new build
// (`scripts/build_atlas_fonts.py`) is uploaded to R2 under its own version and named here.
export const TYPEFACE_VERSIONS = ['bcc12fd5af37', 'c8fbfd1f6e9b'] as const;
export const TYPEFACE_VERSION = TYPEFACE_VERSIONS[0];
// The builds of Sealie, the small seal label font (`scripts/build_seal_font.py`), newest first, stored
// and served the same way. An earlier build stays served: a tab opened before a deploy still asks for it.
export const SEAL_FONT_VERSIONS = ['d1a8cfcec506', '0ef18cf6f52f', 'cf09793173b8', 'a742de4b189c'] as const;
export const SEAL_FONT_VERSION = SEAL_FONT_VERSIONS[0];
export const FONT_VERSIONS: readonly string[] = [...TYPEFACE_VERSIONS, ...SEAL_FONT_VERSIONS];
