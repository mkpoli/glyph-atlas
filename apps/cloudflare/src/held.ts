/** A value read at most once per `lag` seconds. Only a value already read is kept: a read still
 *  pending belongs to its request, and workerd cancels it should that request end first. */
export function held<T>() {
  let copy: { at: number; value: T } | null = null;
  return async (read: () => Promise<T>, now: number, lag: number) => {
    if (copy && now - copy.at < lag * 1000) return copy.value;
    const value = await read();
    // A read that started earlier and finished later is older than the one kept.
    if (!copy || copy.at <= now) copy = { at: now, value };
    return value;
  };
}
