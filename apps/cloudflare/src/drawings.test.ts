import { describe, expect, it } from 'bun:test';
import { drawingFile } from './drawings';

const name = `${'ab'.repeat(32)}.svg`, svg = '<svg xmlns="http://www.w3.org/2000/svg"/>';
const env = { MEDIA: { async get(key: string) {
  if (key !== `ids/${name}`) return null;
  const bytes = new TextEncoder().encode(svg);
  return { body: new Response(bytes).body!, size: bytes.length, httpEtag: '"e"' };
} } } as any;

describe('drawingFile', () => {
  it('serves a stored drawing as an SVG that runs nothing', async () => {
    const response = (await drawingFile(env, name))!;
    expect(await response.text()).toBe(svg);
    expect(response.headers.get('content-type')).toBe('image/svg+xml');
    expect(response.headers.get('content-security-policy')).toContain("default-src 'none'");
  });
  it('answers nothing for a sequence no drawing is made of, or a name that is not a key', async () => {
    expect(await drawingFile(env, `${'cd'.repeat(32)}.svg`)).toBeNull();
    for (const bad of ['../x.svg', 'AB.svg', `${'ab'.repeat(32)}.png`, `ids/${name}`]) expect(await drawingFile(env, bad)).toBeNull();
  });
});
