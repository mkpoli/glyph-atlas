// How long each step of an answer took, as a `Server-Timing` header: a step's time runs from the previous
// step's end (`step`), or from the start for steps that run side by side (`beside`). A Worker's clock moves
// only across I/O, which is what the steps wait on.
export class Timing {
  private start = Date.now();
  private last = this.start;
  private steps: [string, number][] = [];
  step(name: string) { const now = Date.now(); this.steps.push([name, now - this.last]); this.last = now; }
  beside<T>(name: string, work: Promise<T>): Promise<T> {
    const from = Date.now();
    return work.finally(() => { this.steps.push([name, Date.now() - from]); });
  }
  header() { return [...this.steps, ['total', Date.now() - this.start] as [string, number]].map(([name, ms]) => `${name};dur=${ms}`).join(', '); }
}
