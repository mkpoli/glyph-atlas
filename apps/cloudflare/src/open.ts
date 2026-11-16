// Reads any site may make: a document's characters, a citation and the composer's export. Their
// answers carry the header, refusals and errors included, so the asking page can read the status.
export const OPEN = { 'access-control-allow-origin': '*' };
export const opened = (path: string) => path.startsWith('/atlas/documents/') || path.startsWith('/atlas/cite/') || path.startsWith('/atlas/composer/');
