// Keep fixture pages distinct across suites that share the Worker bundle cache.
let page = 7000;
export const nextPage = () => ++page;
