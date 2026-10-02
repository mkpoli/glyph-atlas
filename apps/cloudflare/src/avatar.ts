// A signed-in reader's picture: their initial, the picture of an account elsewhere, their Gravatar, or
// an image they upload. An upload is cropped and encoded as WebP in the browser, so the Worker only
// checks and stores it.
import type { Viewer } from './auth';

type Answer = { status: number; body: Record<string, unknown> };
const UPLOAD_MAX = 256 * 1024;
export const AVATAR_PATH = /^\/api\/avatars\/([0-9a-f-]{36})\/([0-9a-f]{16})\.webp$/;

const hex = (bytes: ArrayBuffer) => [...new Uint8Array(bytes)].map(b => b.toString(16).padStart(2, '0')).join('');
// A WebP file opens with `RIFF`, its length, then `WEBP`.
const isWebp = (bytes: Uint8Array) => bytes.length > 12 && String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF'
  && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP';

/** The address of the Gravatar for an email address, or null when Gravatar has none for it. */
export async function gravatar(email: string): Promise<string | null> {
  const hash = hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(email.trim().toLowerCase())));
  const url = `https://gravatar.com/avatar/${hash}?s=256`;
  const found = await fetch(url + '&d=404', { method: 'HEAD' }).catch(() => null);
  return found?.ok ? url : null;
}

const uploadedKey = (image: string | null) => {
  const match = image?.match(AVATAR_PATH);
  return match ? `avatars/${match[1]}/${match[2]}.webp` : null;
};

/** Set the reader's picture from `source`, the uploaded bytes coming in `request` for `upload`. */
export async function setAvatar(env: Env, me: Viewer, source: string, request: Request): Promise<Answer> {
  if (me.anonymous) return { status: 403, body: { detail: 'Sign in to an account to choose a picture.' } };
  const row = await env.DB.prepare('SELECT email,image FROM "user" WHERE id=?').bind(me.id).first<{ email: string; image: string | null }>();
  if (!row) return { status: 404, body: { detail: 'No such user.' } };
  let image: string | null;
  if (source === 'initial') image = null;
  else if (source === 'gravatar') {
    image = await gravatar(row.email);
    if (!image) return { status: 404, body: { detail: 'Gravatar has no picture for this address.' } };
  } else if (source === 'github') {
    const account = await env.DB.prepare("SELECT accountId FROM account WHERE userId=? AND providerId='github'").bind(me.id).first<{ accountId: string }>();
    if (!account) return { status: 404, body: { detail: 'No GitHub account is connected.' } };
    image = `https://avatars.githubusercontent.com/u/${encodeURIComponent(account.accountId)}?v=4`;
  } else if (source === 'upload') {
    if (request.headers.get('content-type') !== 'image/webp') return { status: 415, body: { detail: 'Send the picture as WebP.' } };
    const bytes = new Uint8Array(await request.arrayBuffer());
    if (bytes.length > UPLOAD_MAX) return { status: 413, body: { detail: 'The picture is too large.' } };
    if (!isWebp(bytes)) return { status: 415, body: { detail: 'Send the picture as WebP.' } };
    const name = hex(await crypto.subtle.digest('SHA-256', bytes)).slice(0, 16);
    await env.MEDIA.put(`avatars/${me.id}/${name}.webp`, bytes, { httpMetadata: { contentType: 'image/webp', cacheControl: 'public, max-age=31536000, immutable' } });
    image = `/api/avatars/${me.id}/${name}.webp`;
  } else return { status: 422, body: { detail: 'Unknown picture source.' } };
  await env.DB.prepare('UPDATE "user" SET image=?,updatedAt=? WHERE id=?').bind(image, new Date().toISOString(), me.id).run();
  // An upload the reader has moved on from is removed.
  const old = uploadedKey(row.image);
  if (old && old !== uploadedKey(image)) await env.MEDIA.delete(old);
  return { status: 200, body: { image } };
}

/** An uploaded picture, which never changes under its name. */
export async function avatar(env: Env, user: string, name: string): Promise<Response> {
  const object = await env.MEDIA.get(`avatars/${user}/${name}.webp`);
  if (!object) return new Response('Not found', { status: 404 });
  return new Response(object.body, { headers: { 'content-type': 'image/webp', 'cache-control': 'public, max-age=31536000, immutable',
    'x-content-type-options': 'nosniff' } });
}
