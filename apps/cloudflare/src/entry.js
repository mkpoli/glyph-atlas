// The deployed Worker: the site SvelteKit builds (`site.wrangler.jsonc` tells its adapter where), which
// answers every request, and the hourly cron.
import site from '../../review/.svelte-kit/cloudflare/_worker.js';
import { hourly } from './accounts.ts';

export default {
  fetch: (request, env, ctx) => site.fetch(request, env, ctx),
  scheduled: (_controller, env) => hourly(env),
};
