// The composer exports the site serves (`composer.ts`), newest first. The review app's IDS editor draws
// its preview from the first. A new export is uploaded to R2 under its own version and named here.
export const COMPOSER_VERSIONS = ['bd0829bc6fbf-sans-compose-layout-v4-f5+letters',
  'bd0829bc6fbf-sans-compose-layout-v4-f5'] as const;
export const COMPOSER_VERSION = COMPOSER_VERSIONS[0];
