// The deployed Worker: the site SvelteKit builds (`site.wrangler.jsonc` tells its adapter where), which
// answers every request, the hourly cron, and mail routed to the contact address (`contact.ts`).
import site from '../../review/.svelte-kit/cloudflare/_worker.js';
import worker from './index.ts';
import { hourly } from './accounts.ts';
import { receiveMail } from './contact.ts';

// The OAuth calls another server makes (the Matrix homeserver exchanging a sign-in code) post forms
// with no Origin, which the site's cross-site form check refuses; they go to the API directly.
const SERVER_TO_SERVER = /^\/api\/auth\/oauth2\/(token|revoke|introspect)$/;

export default {
  fetch: (request, env, ctx) => SERVER_TO_SERVER.test(new URL(request.url).pathname) ? worker.fetch(request, env, ctx) : site.fetch(request, env, ctx),
  scheduled: (_controller, env) => hourly(env),
  email: (message, env) => receiveMail(message, env),
};
