// What a write sends: its JSON body, bounded, and the text fields and characters in it.
import { Problem } from './busy';

type Json = Record<string, any>;

// NFC composes a voiced kana, but it also maps each CJK compatibility ideograph to its unified twin,
// and those are characters of their own here; they are kept as written.
const COMPATIBILITY = /[豈-﫿\u{2F800}-\u{2FA1F}]/u;
export function compose(value: string): string {
  let out = '', run = '';
  for (const c of value) { if (COMPATIBILITY.test(c)) { out += run.normalize('NFC') + c; run = '' } else run += c }
  return out + run.normalize('NFC');
}
export function literal(value: string): string {
  const trimmed = value.trim();
  // A key is written with spaces in the API and with hyphens in a page address.
  if (/^(U\+[0-9a-f]{4,6})([\s-]+U\+[0-9a-f]{4,6})*$/i.test(trimmed)) {
    try { return compose(trimmed.split(/[\s-]+/).map(v => String.fromCodePoint(parseInt(v.slice(2), 16))).join('')) }
    catch { throw new Problem(422, 'Invalid code point.') }
  }
  return compose(trimmed);
}
export const single = (value: string) => [...new Intl.Segmenter('ja', { granularity: 'grapheme' }).segment(value)].length === 1;
export async function body(request: Request): Promise<Json> {
  const origin=request.headers.get('origin');
  if(origin && origin!==new URL(request.url).origin) throw new Problem(403,'Use the review form on this site.');
  if(!request.headers.get('content-type')?.startsWith('application/json')) throw new Problem(415,'Expected JSON.');
  const reader=request.body?.getReader();
  if(!reader) throw new Problem(422,'Empty request.');
  let size=0; const chunks:Uint8Array[]=[];
  while(true) {const {done,value}=await reader.read();if(done)break;size+=value.length;
    if(size>128*1024){await reader.cancel();throw new Problem(413,'Review is too large.')}chunks.push(value)}
  const bytes=new Uint8Array(size);let pos=0;for(const part of chunks){bytes.set(part,pos);pos+=part.length}
  try{return JSON.parse(new TextDecoder().decode(bytes))}catch{throw new Problem(422,'Invalid JSON.')}
}
export function text(value: unknown, max: number, name: string, required=false): string | null {
  if(value==null && !required) return null;
  if(typeof value!=='string'||value.length>max||(required&&!value.trim()))throw new Problem(422,`Invalid ${name}.`);
  return compose(value.trim());
}
export function canonical(value: unknown): string {
  if(value===null||typeof value!=='object')return JSON.stringify(value);
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical((value as Json)[k])).join(',')+'}';
}
// A written character a reviewer names: one character, which may carry combining marks or a variation
// selector, and may be a private-use code point (구결자 are encoded there). Control, format, surrogate
// and separator code points are refused, as is a combining mark with no base.
export function writtenCharacter(value: string): string {
  const written = literal(value);
  if (!written || /[\p{Cc}\p{Cf}\p{Cs}\p{Z}]/u.test(written) || /^\p{M}/u.test(written) || !single(written))
    throw new Problem(422, 'Choose one written character.');
  return written;
}
export const GRAPHEME_KEY = /^U\+[0-9A-F]{4,6}( U\+[0-9A-F]{4,6})*$/;
// A grapheme key (`U+306F`) as the text it names.
export const graphemeText = (key: string) => key.split(' ').map(point => String.fromCodePoint(parseInt(point.slice(2), 16))).join('');
// A grapheme key as a request names it, upper-cased with single spaces, or null when none is named.
export function graphemeKey(value: string | null | undefined): string | null {
  const key = text(value, 256, 'grapheme')?.toUpperCase().split(/\s+/).join(' ');
  if (!key) return null;
  if (!GRAPHEME_KEY.test(key) || key.split(' ').some(p => parseInt(p.slice(2), 16) > 0x10FFFF)) throw new Problem(422, 'Invalid grapheme.');
  return key;
}

export function integer(q: URLSearchParams, key: string, fallback: number, max = 1000000) {
  const n = Number(q.get(key) ?? fallback);
  if (!Number.isSafeInteger(n) || n < 0 || n > max) throw new Problem(422, `Invalid ${key}.`);
  return n;
}
