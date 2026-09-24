// Catalogue snapshots are published offline. All online review mutations use D1 transactions.
type Json = Record<string, any>;
type UnitRow = { id: string; origin: string; character: string; state: string; revision: number;
  quiz: number; category?: string; data: string; snapshot: string; context: string; visual: string };
type CorpusRow = {id:string;character:string|null;family:string|null;visual_group:string|null;shuffle:number;object:string;offset:number;size:number};
class Problem extends Error {
  constructor(public status: number, message: string) { super(message) }
}
const json = (value: unknown, status = 200, headers: HeadersInit = {}) => Response.json(value, {
  status, headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...headers },
});
const parse = (value: string): Json => JSON.parse(value);
const unavailable = { status: 'unavailable', candidates: [] };
const categoryOf=(value:string)=>/[\p{Script=Hiragana}\p{Script=Katakana}]/u.test(value)?'kana':/\p{Script=Han}/u.test(value)?'kanji':'other';
const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
export function literal(value: string): string {
  const trimmed = value.trim();
  if (/^(U\+[0-9a-f]{4,6})(\s+U\+[0-9a-f]{4,6})*$/i.test(trimmed)) {
    try { return trimmed.split(/\s+/).map(v => String.fromCodePoint(parseInt(v.slice(2), 16))).join('').normalize('NFC') }
    catch { throw new Problem(422, 'Invalid code point.') }
  }
  return trimmed.normalize('NFC');
}
export const hira = (value: string) => [...literal(value)].map(c => {
  const n = c.codePointAt(0)!; return n >= 0x30a1 && n <= 0x30f6 ? String.fromCodePoint(n - 0x60) : c;
}).join('');
export const single = (value: string) => [...new Intl.Segmenter('ja', { granularity: 'grapheme' }).segment(value)].length === 1;
function integer(q: URLSearchParams, key: string, fallback: number, max = 1000000) {
  const n = Number(q.get(key) ?? fallback);
  if (!Number.isSafeInteger(n) || n < 0 || n > max) throw new Problem(422, `Invalid ${key}.`);
  return n;
}
async function meta(env: Env, key: string): Promise<any> {
  const row = await env.DB.prepare('SELECT value FROM metadata WHERE key=?').bind(key).first<{ value: string }>();
  return row ? JSON.parse(row.value) : null;
}
async function unit(env: Env, id: string): Promise<UnitRow> {
  const row = await env.DB.prepare('SELECT * FROM units WHERE id=?').bind(id).first<UnitRow>();
  if(row)return row;
  const pointer=await env.DB.prepare('SELECT * FROM corpus_units WHERE id=?').bind(id).first<CorpusRow>();
  if(!pointer)throw new Problem(404, 'This character is not in the published collection.');
  const data=await corpusData(env,pointer);
  return {id,origin:'corpus',character:data.label,state:data.state,revision:data.revision,quiz:0,
    data:JSON.stringify(data),snapshot:JSON.stringify(data),context:JSON.stringify(unavailable),visual:JSON.stringify(unavailable)};
}
async function corpusData(env:Env,row:CorpusRow):Promise<Json>{
  if(row.size>128*1024)throw new Problem(503,'Invalid published record.');
  const object=await env.MEDIA.get(row.object,{range:{offset:row.offset,length:row.size}});
  if(!object)throw new Problem(503,'The corpus publication is incomplete.');
  return object.json<Json>();
}
function compact(row: UnitRow): Json {
  const d = parse(row.data);
  const { text, line, context_image, context_box, crop_box, ...rest } = d;
  return rest;
}
async function catalogue(env: Env, q: URLSearchParams) {
  const purpose = q.get('purpose') || 'browse';
  const production = q.get('production') || (purpose === 'review' ? 'non-movable-type' : 'all');
  const where = ["origin='local'"];
  const values: (string | number)[] = [];
  if (purpose === 'review') where.push('quiz=1');
  if (production === 'non-movable-type') where.push("production!='movable-type'");
  else if (production !== 'all') { where.push('production=?'); values.push(production) }
  const groups = await env.DB.prepare(`SELECT character AS label,state,count(*) AS n FROM units WHERE ${where.join(' AND ')} GROUP BY character,state`).bind(...values).all<{label:string;state:string;n:number}>();
  const categories = new Map<string, Json>();
  const counts: Json = { pending: 0, flagged: 0, checked: 0 };
  for (const row of groups.results) {
    const category = categories.get(row.label) || { label: row.label, total: 0, pending: 0, checked: 0, flagged: 0 };
    category.total += row.n; category[row.state] += row.n; counts[row.state] += row.n;
    categories.set(row.label, category);
  }
  if (q.get('reading')) { where.push('character=?'); values.push(q.get('reading')!) }
  if (q.get('q')) { where.push('(character=? OR reading=?)'); values.push(literal(q.get('q')!), literal(q.get('q')!)) }
  if (q.get('group') && q.get('group') !== 'all') { where.push('category=?'); values.push(q.get('group')!) }
  if (q.get('state') && q.get('state') !== 'all') { where.push('state=?'); values.push(q.get('state')!) }
  const seed = integer(q, 'seed', 0, 2147483647);
  const limit = integer(q, 'limit', 60, 96), offset = integer(q, 'offset', 0);
  const order = purpose === 'review' && seed % 5 ? 'priority,' : '';
  const [count, window] = await env.DB.batch([
    env.DB.prepare(`SELECT count(*) AS n FROM units WHERE ${where.join(' AND ')}`).bind(...values),
    env.DB.prepare(`SELECT * FROM units WHERE ${where.join(' AND ')} ORDER BY ${order} ((shuffle * ?) % 2147483647),id LIMIT ? OFFSET ?`).bind(...values, seed + 1, limit, offset),
  ]);
  return { total: (count.results[0] as { n: number }).n, available: Object.values(counts).reduce((a:number,b:any) => a+b,0),
    counts, purpose, production, review_limit:96, review_epoch: await meta(env, 'review_epoch') || 0, query: q.get('q'),
    categories: [...categories.values()].sort((a,b) => b.total-a.total || a.label.localeCompare(b.label)),
    items: (window.results as UnitRow[]).map(compact) };
}
async function known(env: Env, value: string) {
  const key = cp(literal(value));
  const row = await env.DB.prepare('SELECT data,detail FROM characters WHERE code_point=?').bind(key).first<{data:string;detail:string}>();
  if (!row) throw new Problem(404, 'Character not found.');
  return { data: parse(row.data), detail: parse(row.detail) };
}
async function suggest(env: Env, q: URLSearchParams) {
  const term = (q.get('q') || '').slice(0, 128).trim();
  if (!term) return { items: [], total: 0, status: 'idle' };
  const limit = integer(q, 'limit', 8, 48);
  const rows = await env.DB.prepare(`SELECT c.data,min(a.rank) AS rank FROM aliases a JOIN characters c ON c.code_point=a.code_point
    WHERE a.query IN (?,?,?) GROUP BY c.code_point ORDER BY rank,CAST(json_extract(c.data,'$.occurrence_count') AS INTEGER) DESC,c.code_point LIMIT ?`)
    .bind(literal(term), hira(term), term.toLowerCase(), limit + 1).all<{ data: string; rank: number }>();
  if (!rows.results.length && /^[a-z0-9 -]+$/i.test(term)) {
    const found = await env.DB.prepare('SELECT data,5 AS rank FROM characters WHERE name LIKE ? LIMIT ?').bind('%'+term+'%',limit+1).all<{data:string;rank:number}>();
    rows.results.push(...found.results);
  }
  return { query: term, items: rows.results.slice(0,limit).map(r => ({...parse(r.data), rank:r.rank})),
    total: rows.results.length, more: Math.max(0,rows.results.length-limit), status:'ok',corpus:{ready:true} };
}
async function occurrences(env: Env, code: string, q: URLSearchParams, origin = 'local') {
  const { data } = await known(env, code);
  if(origin==='corpus')return corpusOccurrences(env,data,q);
  const limit = integer(q,'limit',24,200), offset=integer(q,'offset',0);
  const values: (string | number)[] = [origin];
  const where = ['origin=?'];
  if (q.get('scope') === 'grapheme' || q.get('expand') === 'grapheme') {
    const family = data.grapheme?.code_point || data.code_point;
    where.push('(family=? OR character=?)'); values.push(family,data.char);
  } else { where.push('character=?'); values.push(data.char) }
  if (q.get('visual_group')) {
    if(q.get('visual_group')==='unassigned') where.push('visual_group IS NULL');
    else { where.push('visual_group=?'); values.push(q.get('visual_group')!) }
  }
  if (q.get('state') && q.get('state')!=='all') { where.push('state=?'); values.push(q.get('state')!) }
  const [count, rows] = await env.DB.batch([
    env.DB.prepare(`SELECT count(*) AS n FROM units WHERE ${where.join(' AND ')}`).bind(...values),
    env.DB.prepare(`SELECT * FROM units WHERE ${where.join(' AND ')} ORDER BY id LIMIT ? OFFSET ?`).bind(...values,limit,offset),
  ]);
  const total = (count.results[0] as {n:number}).n;
  return { ...data.candidates, query: data.code_point, code_point: data.code_point,
    total, available: rows.results.length, items:(rows.results as UnitRow[]).map(compact),
    counts:{ total, exact:total, exact_total:total }, scope:q.get('scope') || 'character', status:'ok' };
}
async function corpusOccurrences(env:Env,data:Json,q:URLSearchParams){
  const limit=integer(q,'limit',24,200),offset=integer(q,'offset',0);
  const family=q.get('scope')==='grapheme',field=family?'family':'character';
  const selected=family?(data.grapheme?.code_point||data.code_point):data.char;
  // Two indexed branches include corrections into this category and remove corrections
  // out of it. Unassigned source classes remain null until evidence identifies the form.
  const selection=`SELECT c.*,u.data AS overlay,u.visual_group AS overlay_group,u.character AS overlay_character
    FROM corpus_units c LEFT JOIN units u ON c.id=u.id
    WHERE c.${field}=? AND (u.id IS NULL OR u.${field}=c.${field})
    UNION ALL SELECT c.*,u.data AS overlay,u.visual_group AS overlay_group,u.character AS overlay_character
    FROM units u JOIN corpus_units c ON c.id=u.id
    WHERE u.origin='corpus' AND u.${field}=? AND c.${field} IS NOT u.${field}`;
  const where=['1=1'],values:(string|number)[]=[selected,selected];
  if(q.get('visual_group')){if(q.get('visual_group')==='unassigned')where.push('(CASE WHEN overlay IS NULL THEN character ELSE overlay_character END) IS NULL');
    else{where.push('(CASE WHEN overlay IS NULL THEN visual_group ELSE overlay_group END)=?');values.push(q.get('visual_group')!)}}
  const join=`FROM (${selection})`;
  const [count,rows]=await env.DB.batch([
    env.DB.prepare(`SELECT count(*) AS n ${join} WHERE ${where.join(' AND ')}`).bind(...values),
    env.DB.prepare(`SELECT * ${join} WHERE ${where.join(' AND ')} ORDER BY id LIMIT ? OFFSET ?`).bind(...values,limit,offset),
  ]);
  const items=[];
  // Bound simultaneous R2 streams; a corpus page may contain 200 records.
  for(let i=0;i<rows.results.length;i+=8){
    items.push(...await Promise.all((rows.results.slice(i,i+8) as (CorpusRow&{overlay:string|null})[])
      .map(async row=>row.overlay?parse(row.overlay):await corpusData(env,row))));
  }
  const info=await known(env,data.char),familyCode=data.grapheme?.code_point||data.code_point;
  const familyCounts=await env.DB.prepare(`SELECT count(*) AS total,sum(written IS NULL) AS unassigned FROM (
    SELECT CASE WHEN u.id IS NULL THEN c.character ELSE u.character END AS written
      FROM corpus_units c LEFT JOIN units u ON c.id=u.id WHERE c.family=? AND (u.id IS NULL OR u.family=c.family)
    UNION ALL SELECT u.character AS written FROM units u JOIN corpus_units c ON c.id=u.id
      WHERE u.origin='corpus' AND u.family=? AND c.family IS NOT u.family
    )`).bind(familyCode,familyCode).first<{total:number;unassigned:number}>();
  return {...data.candidates,code_point:data.code_point,total:(count.results[0] as {n:number}).n,
    available:items.length,items,scope:family?'grapheme':'character',status:'ok',
    visual_analysis:info.detail.visual_analysis,family_total:familyCounts?.total||0,unassigned_count:familyCounts?.unassigned||0};
}
async function media(env: Env, request: Request, key: string, ctx: ExecutionContext) {
  const cache = caches.default;
  const cached = await cache.match(request);
  if (cached) return cached;
  if (!/^[a-f0-9]{64}$/.test(key)) throw new Problem(404,'Image not found.');
  const row = await env.DB.prepare('SELECT * FROM media WHERE key=?').bind(key)
    .first<{ object:string;offset:number;size:number;content_type:string }>();
  if(!row) throw new Problem(404,'Image not found.');
  if(request.headers.get('if-none-match')===`"${key}"`) return new Response(null,{status:304});
  const object=await env.MEDIA.get(row.object,{range:{offset:row.offset,length:row.size}});
  if(!object) throw new Problem(503,'Image publication is incomplete.');
  const response=new Response(object.body,{headers:{'content-type':row.content_type,'content-length':String(row.size),
    'cache-control':'public, max-age=31536000, immutable','etag':`"${key}"`,'x-content-type-options':'nosniff'}});
  ctx.waitUntil(cache.put(request,response.clone()));
  return response;
}
async function body(request: Request): Promise<Json> {
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
function text(value: unknown, max: number, name: string, required=false): string | null {
  if(value==null && !required) return null;
  if(typeof value!=='string'||value.length>max||(required&&!value.trim()))throw new Problem(422,`Invalid ${name}.`);
  return value.trim().normalize('NFC');
}
export function canonical(value: unknown): string {
  if(value===null||typeof value!=='object')return JSON.stringify(value);
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical((value as Json)[k])).join(',')+'}';
}
function validateAnswer(answer: Json, current: Json, round: boolean, corpus=false) {
  if(!Number.isSafeInteger(answer.revision)||answer.revision!==current.revision)throw new Problem(409,'This character changed. Reload it.');
  if(corpus?answer.source_revision!==current.source_revision:answer.image_sha256!==current.image_sha256)throw new Problem(409,'The source image changed. Reload it.');
  if(!['match','wrong','unsure'].includes(answer.verdict))throw new Problem(422,'Choose a review decision.');
  if(answer.issue!=null&&!['character','reading','merged','crop','blank','other','unclear'].includes(answer.issue))throw new Problem(422,'Unknown issue.');
  if(answer.verdict==='match'&&(answer.character||answer.correction||(round&&answer.issue)||(!round&&answer.issue&&answer.issue!=='reading')))
    throw new Problem(422,'A matching crop cannot also have an issue.');
  if(answer.verdict==='wrong'&&!answer.issue)throw new Problem(422,'Choose an issue.');
  for(const field of ['character','correction','reading'])text(answer[field],32,field);
  text(answer.note,2000,'note');
  if(answer.character && !single(literal(answer.character)))throw new Problem(422,'Choose one written character.');
  if(answer.verdict==='wrong' && answer.character && literal(answer.character)===current.written_character)
    throw new Problem(422,'Choose a different character or a different issue.');
  if(answer.box) throw new Problem(422,'Crop geometry changes are queued as crop issues on this publication.');
}
async function submit(env: Env, request: Request, target?: string) {
  const input=await body(request);
  const corpus=target==='@corpus';
  if(corpus)target=text(input.identity,512,'corpus identity',true)!;
  const id=text(input.id,64,'submission id',true)!, actor=text(input.client_id,128,'reviewer',true)!;
  if(!/^[0-9a-f-]{36}$/i.test(id))throw new Problem(422,'Invalid submission id.');
  const signature=canonical({target:target||null,input});
  const key=actor+':'+id;
  const previous=await env.DB.prepare('SELECT request,response FROM submissions WHERE id=?').bind(key).first<{request:string;response:string}>();
  if(previous){if(previous.request!==signature)throw new Problem(409,'This submission was already saved with different answers.');return parse(previous.response)}
  const round=!target;
  const answers:Json[]=round?input.answers:[{...input,id:target}];
  if(!Array.isArray(answers)||answers.length<1||answers.length>96||new Set(answers.map(a=>a.id)).size!==answers.length)
    throw new Problem(422,'A round needs 1–96 distinct crops.');
  const changes=[];
  const at=new Date().toISOString();
  for(const answer of answers){
    text(answer.id,512,'character id',true);
    const row=await unit(env,answer.id), stored=parse(row.data);
    const current:Json={...stored,category:row.category||categoryOf(stored.label)};
    row.data=JSON.stringify(current);
    validateAnswer(answer,current,round,corpus);
    if(answer.reading&&!single(answer.reading)){
      const identity=answer.character||current.written_character||current.label;
      const registered=await known(env,identity).catch(()=>null);
      if(!registered?.data.ligature?.reading||hira(registered.data.ligature.reading)!==hira(answer.reading))
        throw new Problem(422,'Use the registered ligature reading or one character.');
    }
    if(corpus&&(!current.proxyable||(current.identity_status==='unassigned'&&answer.verdict==='match')))
      throw new Problem(422,'Choose a written character or report an issue.');
    if(round&&(!row.quiz||current.label!==input.label))throw new Problem(409,'This round changed. Reload it.');
    const written=answer.character?literal(answer.character):null;
    const reading=answer.reading || (answer.issue==='reading'&&answer.correction&&single(answer.correction)?answer.correction:null);
    const resolved=answer.verdict==='match'||Boolean(answer.issue==='character'&&written)||Boolean(answer.issue==='reading'&&reading);
    const family=written?(await known(env,written).catch(()=>null))?.data.grapheme?.code_point:null;
    const next:Json={...current,revision:current.revision+1,state:resolved?'checked':'flagged',
      ...(written?{label:written,char:written,code_point:cp(written),written_character:written,identity_status:'assigned',identity_basis:'human_review',script:/\p{Script=Katakana}/u.test(written)?'katakana':/\p{Script=Hiragana}/u.test(written)?'hiragana':/\p{Script=Han}/u.test(written)?'han':'symbol'}:{}),
      ...(written?{grapheme:family||cp(written),visual_group:null,category:categoryOf(written)}:{}),
      ...(reading?{reading}:{}),issue:resolved?null:answer.issue};
    const snapshot={...parse(row.snapshot),character:compact(row)};
    const evidence={kind:round?'visual-quiz':'character-review',...(round?{round:id,label:input.label}:{}),
      request:input,verdict:answer.verdict,issue:answer.issue||null,note:answer.note||'',
      suggested_character:written?cp(written):null,suggested_reading:answer.correction||null,snapshot,
      correction:{unicode:cp(next.label),reading:next.reading,box:next.box}};
    const event={id:'cf:'+crypto.randomUUID(),target_type:'unit',target_id:row.id,field:'review',
      old:current.state==='checked'?'reviewed':current.state==='flagged'?'disputed':'machine',
      new:resolved?'reviewed':'disputed',role:'reviewer',actor,evidence:JSON.stringify(evidence),at};
    changes.push({row,next,event,snapshot});
  }
  const result=corpus?{...(changes[0].next),origin:'corpus',event:changes[0].event}
    :{id,results:changes.map(c=>({id:c.event.id,target_id:c.row.id,field:'review',revision:c.next.revision,review:c.event}))};
  const statements=[env.DB.prepare('INSERT INTO submissions(id,actor,request,response,at) VALUES (?,?,?,?,?)').bind(key,actor,signature,JSON.stringify(result),at)];
  for(const c of changes){
    if(corpus){const d=parse(c.row.data);statements.push(env.DB.prepare('INSERT OR IGNORE INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
      .bind(c.row.id,'corpus',d.written_character||null,d.reading||null,d.grapheme||null,d.visual_group?.id||null,d.production||'unknown','other',d.state,d.revision,0,1,0,c.row.data,c.row.snapshot,c.row.context,c.row.visual))}
    statements.push(env.DB.prepare(`INSERT INTO events(id,submission,target,actor,expected_revision,before_data,after_data,event,snapshot,kind,at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`)
      .bind(c.event.id,key,c.row.id,actor,c.row.revision,c.row.data,JSON.stringify(c.next),JSON.stringify(c.event),corpus?c.row.snapshot:JSON.stringify(c.snapshot),'review',at));
  }
  try{await env.DB.batch(statements)}catch(error){
    const repeat=await env.DB.prepare('SELECT request,response FROM submissions WHERE id=?').bind(key).first<{request:string;response:string}>();
    if(repeat?.request===signature)return parse(repeat.response);
    if(String(error).includes('review_revision_conflict'))throw new Problem(409,'Another review changed this crop. Reload it.');
    throw error;
  }
  return result;
}
async function undo(env:Env,request:Request,id:string){
  const input=await body(request),actor=text(input.client_id,128,'reviewer',true)!,key=actor+':'+id;
  const submission=await env.DB.prepare('SELECT * FROM submissions WHERE id=?').bind(key).first<Json>();
  if(!submission)throw new Problem(404,'No saved round belongs to this reviewer.');
  if(submission.undone)return {id,results:[],duplicate:true};
  const rows=await env.DB.prepare("SELECT * FROM events WHERE submission=? AND kind='review'").bind(key).all<Json>();
  const statements:D1PreparedStatement[]=[],results=[];const at=new Date().toISOString();
  for(const r of rows.results){
    const current=await unit(env,r.target);
    if(current.revision!==r.expected_revision+1)throw new Problem(409,'A later review changed this crop. It cannot be undone.');
    const restored={...parse(r.before_data),revision:current.revision+1};
    const event={...parse(r.event),id:'cf:'+crypto.randomUUID(),old:parse(r.event).new,new:parse(r.event).old,evidence:'undo of '+r.id,at};
    statements.push(env.DB.prepare('INSERT INTO events(id,submission,target,actor,expected_revision,before_data,after_data,event,snapshot,kind,at) VALUES(?,?,?,?,?,?,?,?,?,?,?)')
      .bind(event.id,key,r.target,actor,current.revision,current.data,JSON.stringify(restored),JSON.stringify(event),r.snapshot,'undo',at));
    // Restore queue eligibility from the published record when undoing an issue.
    statements.push(env.DB.prepare('UPDATE units SET quiz=? WHERE id=?').bind(current.origin==='corpus'||parse(r.before_data).repair?.quiz===false?0:1,r.target));
    results.push({id:event.id,target_id:r.target,revision:restored.revision,review:event});
  }
  statements.push(env.DB.prepare('UPDATE submissions SET undone=1 WHERE id=?').bind(key));
  try{await env.DB.batch(statements)}catch(error){if(String(error).includes('review_revision_conflict'))throw new Problem(409,'A later review changed this crop.');throw error}
  return {id,results};
}
async function reviews(env:Env,all:boolean){
  const rows=await env.DB.prepare(`SELECT e.event,e.snapshot,e.expected_revision,u.revision,u.origin,e.id,u.snapshot AS publication_snapshot,
    EXISTS(SELECT 1 FROM events newer WHERE newer.target=e.target AND newer.expected_revision>e.expected_revision) AS superseded
    FROM events e JOIN units u ON u.id=e.target WHERE e.kind='review' ${all?'':'AND e.processed=0'} ORDER BY e.at`).all<Json>();
  return {version:1,kind:'atlas-character-reviews',reviews:rows.results.map(r=>{
    const event=parse(r.event),snapshot=parse(r.snapshot);
    if(r.origin==='corpus'){const e=parse(event.evidence);return {origin:'corpus',event:{...event,new:{verdict:e.verdict,issue:e.issue,
      character:e.suggested_character?literal(e.suggested_character):null,correction:e.suggested_reading,note:e.note}},
      reviewed:snapshot,current:!r.superseded,current_revision:r.revision,
      source_update:{identity:event.target_id,source_revision:snapshot.source_revision,source:snapshot.source,box:snapshot.box,
        original_character:snapshot.source_code_point?literal(snapshot.source_code_point):snapshot.source_label,
        proposed_character:e.suggested_character?literal(e.suggested_character):null,issue:e.issue,applied_upstream:false}}}
    return {event,reviewed:snapshot,current:!r.superseded,current_revision:r.revision,
      expected_revision:r.expected_revision,publication_snapshot:parse(r.publication_snapshot)}
  }),publication:await meta(env,'published_at')};
}
export default {
  async fetch(request:Request,env:Env,ctx:ExecutionContext):Promise<Response>{
    const url=new URL(request.url),path=url.pathname,q=url.searchParams;
    try{
      if(request.method==='POST'){
        if(path==='/atlas/corpus/reviews')return json(await submit(env,request,'@corpus'));
        if(path==='/atlas/rounds')return json(await submit(env,request));
        const undone=path.match(/^\/atlas\/rounds\/([^/]+)\/undo$/);
        if(undone)return json(await undo(env,request,decodeURIComponent(undone[1])));
        const edit=path.match(/^\/(?:atlas\/characters|layers\/units)\/([^/]+)$/);
        if(edit)return json(await submit(env,request,decodeURIComponent(edit[1])));
        throw new Problem(404,'Unknown endpoint.');
      }
      if(!['GET','HEAD'].includes(request.method))throw new Problem(405,'Method not allowed.');
      if(path==='/health')return json({ok:true,published_at:await meta(env,'published_at')});
      const image=path.match(/^\/atlas\/media\/([a-f0-9]{64})\.webp$/);
      if(image)return await media(env,request,image[1],ctx);
      if(path==='/atlas')return json(await catalogue(env,q));
      if(path==='/atlas/corpus/character')return json(parse((await unit(env,q.get('id')||'')).data));
      if(path==='/atlas/collection/status')return json(await meta(env,'collection'));
      const visualSample=path.match(/^\/layers\/visual-groups\/samples\/([^/]+)\/image$/);
      if(visualSample){const data=parse((await unit(env,decodeURIComponent(visualSample[1]))).data);
        if(!data.image)throw new Problem(404,'Image not found.');
        return Response.redirect(new URL(data.image,url).href,302)}
      if(path==='/atlas/reviews'||path==='/atlas/reviews.json')return json(await reviews(env,q.get('include_processed')==='true'),200,
        path.endsWith('.json')?{'content-disposition':'attachment; filename="atlas-character-reviews.json"'}:{});
      const character=path.match(/^\/atlas\/characters\/([^/]+)(\/suggestions(?:\/context)?)?$/);
      if(character){const row=await unit(env,decodeURIComponent(character[1]));
        if(character[2]){
          if(q.get('revision')!==String(row.revision)||q.get('image_sha256')!==parse(row.data).image_sha256)throw new Problem(409,'Character changed.');
          return json(parse(character[2].endsWith('/context')?row.context:row.visual));
        }
        return json(parse(row.data));
      }
      if(path==='/layers/suggest')return json(await suggest(env,q));
      if(path==='/layers/search'){const found=await suggest(env,q);return json({...found,results:found.items,match:found.items[0]||null})}
      const layer=path.match(/^\/layers\/characters\/([^/]+)$/);
      if(layer){const value=decodeURIComponent(layer[1]),{detail}=await known(env,value);const found=await occurrences(env,value,q);
        return json({...detail,query:detail.code_point,samples:found.items,occurrences:{...found.counts,filtered:found.total}})}
      if(path==='/layers/occurrences')return json(await occurrences(env,q.get('code_point')||'',q));
      if(path==='/layers/candidates'){const found=await occurrences(env,q.get('code_point')||'',q,'corpus');
        return json({...found,glyphs:found.total,glyph_items:found.items,retry:false})}
      if(path==='/layers/gallery'){
        const limit=integer(q,'limit',24,96),seed=integer(q,'seed',0,2147483647)%268435456;
        const rows=await env.DB.prepare('SELECT * FROM corpus_units WHERE shuffle>=? ORDER BY shuffle LIMIT ?').bind(seed,limit).all<CorpusRow>();
        if(rows.results.length<limit){const more=await env.DB.prepare('SELECT * FROM corpus_units WHERE shuffle<? ORDER BY shuffle LIMIT ?').bind(seed,limit-rows.results.length).all<CorpusRow>();rows.results.push(...more.results)}
        const overlays=rows.results.length?await env.DB.prepare(`SELECT id,data FROM units WHERE id IN (${rows.results.map(()=>'?').join(',')})`)
          .bind(...rows.results.map(r=>r.id)).all<{id:string;data:string}>():{results:[]};
        const current=new Map(overlays.results.map(r=>[r.id,r.data]));
        const items=[];for(let i=0;i<rows.results.length;i+=8)items.push(...await Promise.all(rows.results.slice(i,i+8).map(async r=>
          current.has(r.id)?parse(current.get(r.id)!):await corpusData(env,r))));
        return json({status:'ok',available:items.length,items})}
      if(path==='/layers/summary')return json(await meta(env,'corpus_index'));
      if(path==='/layers/graphemes'||path==='/layers/ligatures'){
        const selector=path.endsWith('ligatures')?"json_extract(data,'$.ligature') IS NOT NULL":"json_array_length(json_extract(data,'$.grapheme.members'))>1";
        const rows=await env.DB.prepare(`SELECT data FROM characters WHERE ${selector} LIMIT ? OFFSET ?`).bind(integer(q,'limit',200,500),integer(q,'offset',0)).all<{data:string}>();
        return json({items:rows.results.map(r=>parse(r.data)),total:rows.results.length})}
      if(path==='/atlas/corpus/reviews'){
        const rows=await env.DB.prepare("SELECT * FROM units WHERE origin='corpus' AND state='flagged' ORDER BY id LIMIT 96").all<UnitRow>();
        return json({items:rows.results.map(compact),total:rows.results.length})}
      if(path.startsWith('/atlas')||path.startsWith('/layers')||path.startsWith('/images/'))throw new Problem(404,'Unknown endpoint.');
      return await env.ASSETS.fetch(request);
    }catch(error){
      if(error instanceof Problem)return json({detail:error.message},error.status);
      console.error(JSON.stringify({event:'request_failed',path,error:error instanceof Error?error.name:'unknown'}));
      return json({detail:'The request could not be completed. Please retry.'},503);
    }
  },
} satisfies ExportedHandler<Env>;
