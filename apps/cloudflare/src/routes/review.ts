// Placeholder for the review writes' module, which the review port fills: its exports, typed as that
// port defines them, so the admin routes compile and call them. Each throws until then.
type Json = Record<string, any>;
type Box = { x: number; y: number; w: number; h: number };

export type Submission = { id: number; key: string; actor: number; at: number; undone: number };
export type CropChange = { crop: number; expected: number; cut: number; kind: number; label: string | null; state: number;
  issue: number | null; text?: string | null; note?: string | null; named?: string | null; shown?: number | null;
  quiz?: number | null; category?: number | null; box?: Box | null; reverses?: number | null; was_state?: number | null;
  was_issue?: number | null; was_box?: Box | null };

const missing = () => new Error('The review writes are not ported yet.');

/** Undo a submission as `actor`; a rejection is the same undo made by an admin, recorded with its reason. */
export async function revert(_env: Env, _submission: Submission, _actor: number,
  _rejection?: { by: string; reason: string; reviewer?: number[] }): Promise<{ results: Json[]; duplicate?: true }> {
  throw missing();
}
/** The actor id of a user's own writes, made on first use. */
export async function actorFor(_env: Env, _user: string): Promise<number> { throw missing(); }
/** The statements of crop changes, after the submission row `key` in the same batch. */
export function cropChanges(_env: Env, _actor: number, _key: string, _at: number, _changes: CropChange[]): D1PreparedStatement[] {
  throw missing();
}
/** The statement that adds to an actor's totals. */
export function actorStats(_env: Env, _actor: number, _reviews: number, _rounds: number, _at: number): D1PreparedStatement {
  throw missing();
}
