// The deployed Worker: the site SvelteKit builds (`site.wrangler.jsonc` tells its adapter where), which
// answers every request, the hourly cron, and mail routed to the contact address (`contact.ts`).
import site from '../../review/.svelte-kit/cloudflare/_worker.js';
import { hourly } from './accounts.ts';
import { receiveMail } from './contact.ts';

export default {
  fetch: (request, env, ctx) => site.fetch(request, env, ctx),
  scheduled: (_controller, env) => hourly(env),
  email: (message, env) => receiveMail(message, env),
};
