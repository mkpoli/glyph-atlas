// Catalogue snapshots are published offline. All online review mutations use D1 transactions.
import { body, canonical, integer, literal, single, text } from './input';
export { canonical, compose, literal, single } from './input';
export { recordRanges } from './records';
import { type FormTools } from './forms';
import { formsRoute } from './routes/forms';
import { similarCrops } from './similar';
import { cropParam, movedPathResponse } from './atlas3Crops';
import { drawingFile } from './drawings';
import { composerFile } from './composer';
import { MAX_BODY, QueryError, addressKey, boundedText, modelFile, modelInfo, query as imageQuery } from './reverse';
import { structureOf, suggest, wordChoices } from './routes/search';
import { auth, claim, providers, viewer } from './auth';
import { AVATAR_PATH, avatar, setAvatar } from './avatar';
import { ranking } from './routes/ranking';
import { connections } from './connections';
import { flagCorpus, flaggedCorpus, reject, reviewerActors, reviewers, submissions } from './routes/admin';
import { history } from './routes/history';
import { reviewsPage } from './routes/reviews';
import { listingItems } from './atlas3Listing';
import { StaleCrop, staleResponse } from './atlas3Crops';
import { Problem, READ_BUDGET, RETRY_AFTER, StorageUnavailable, described, retried, transient } from './busy';
import { reviewRoute } from './routes/review';
import { pausedCallback, pausedResponse, pausedSignOut, writes, writesPaused } from './freeze';
import { claimRead, claimWrite, type ClaimTools } from './routes/claims';
import { FavouriteError, favouriteCrops, favouriteIds, setFavourite } from './routes/favourites';
import { MediaIncomplete, mediaResponse } from './atlas3Media';
import { cropRoute } from './routes/crop';
import { runsRoute } from './routes/runs';
import { Timing } from './timing';
import { sendContact } from './contact';
import { communityCounts } from './community';
import { cards, characterCard, characterPage, chronology, citeCharacter, corpusOccurrences, decades, occurrences, type CharacterTools } from './routes/character';
import * as browse from './routes/browse';
export { claimHistoryQuery, currentClaimsQuery, ledgerActionsQuery, ledgerClaimsQuery, resolveClearQuery, resolveWriteQuery } from './ledger';
type Json = Record<string, any>;
const json = (value: unknown, status = 200, headers: HeadersInit = {}) => Response.json(value, {
  status, headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...headers },
});
const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
const toneMark = (char: string) => [0x302E, 0x302F].includes(char.codePointAt(0)!);
const choseong = (char: string) => {
  const n = char.codePointAt(0)!; return (n >= 0x1100 && n <= 0x115F) || (n >= 0xA960 && n <= 0xA97F);
};
const jungseong = (char: string) => {
  const n = char.codePointAt(0)!; return (n >= 0x1160 && n <= 0x11A7) || (n >= 0xD7B0 && n <= 0xD7C6);
};
const jongseong = (char: string) => {
  const n = char.codePointAt(0)!; return (n >= 0x11A8 && n <= 0x11FF) || (n >= 0xD7CB && n <= 0xD7FF);
};
const FILLERS = [0x115F, 0x1160];
/** An old-Hangul syllable Unicode has no precomposed code point for, checked in NFD so that the NFC
 * spelling 셰 + ᇰ of U+1109 U+1168 U+11F0 is one; text NFC would change has another key. */
export const jamoSyllable = (value: string) => {
  if (!single(value) || [...value].length < 2 || value.normalize('NFC') !== value) return false;
  const chars = [...value.normalize('NFD')]; let i = 0;
  if (chars.every(c => FILLERS.includes(c.codePointAt(0)!))) return false;
  while (i < chars.length && choseong(chars[i])) i++;
  if (!i) return false;
  const vowels = i;
  while (i < chars.length && jungseong(chars[i])) i++;
  if (i === vowels) return false;
  while (i < chars.length && jongseong(chars[i])) i++;
  if (i < chars.length && toneMark(chars[i])) i++;
  return i === chars.length;
};
export const hira = (value: string) => [...literal(value)].map(c => {
  const n = c.codePointAt(0)!; return n >= 0x30a1 && n <= 0x30f6 ? String.fromCodePoint(n - 0x60) : c;
}).join('');
const OPEN = { 'access-control-allow-origin': '*' };
// Reads any site may make: a document's characters and a citation.
const opened = (path: string) => path.startsWith('/atlas/documents/') || path.startsWith('/atlas/cite/');
// A character's grapheme family, as a reviewed correction takes it; one the catalogue lacks is its own.
const characterTools: CharacterTools = { literal, jamoSyllable };
const formTools: FormTools = {fail:(status,message,extra)=>{throw new Problem(status,message,extra)},body,text,codePoints:cp};
// What the ledger's routes need of the rest of the Worker (`routes/claims.ts`).
const claimTools: ClaimTools = {fail:(status,message,extra)=>{throw new Problem(status,message,extra)},body,text,canonical:value=>canonical(value),literal,integer};
const routes = {
  async fetch(request:Request,env:Env,ctx:ExecutionContext):Promise<Response>{
    const url=new URL(request.url),path=url.pathname,q=url.searchParams;
    try{
      // The write freeze: one check before any route, so no write is reached while it holds.
      if(writesPaused(env)&&writes(request.method,path)){
        if(request.method==='POST'&&path==='/api/auth/sign-out')return pausedSignOut(request,auth(env,url.origin).options);
        if(path.startsWith('/api/auth/callback/'))return pausedCallback(url.origin);
        return pausedResponse(opened(path)?OPEN:{});
      }
      if(path.startsWith('/api/auth/'))return await auth(env,url.origin).handler(request);
      if(request.method==='POST'&&path==='/atlas/similar/query'){
        // An image search writes nothing and needs no session; one address is held to a rate. The
        // body is a vector, never an image, and it is neither stored nor logged.
        const {success}=await env.REVERSE_QUERIES.limit({key:addressKey(request.headers.get('cf-connecting-ip'))});
        if(!success)throw new Problem(429,'Too many image searches at once. Wait a minute and try again.');
        const text=await boundedText(request,MAX_BODY);
        if(text===null)throw new Problem(413,'The search request is too large.');
        let input:Json;try{input=JSON.parse(text)}catch{throw new Problem(400,'The search request is not JSON.')}
        if(!input||typeof input!=='object'||Array.isArray(input))throw new Problem(400,'The search request is not an object.');
        try{return json(await imageQuery(env,input,listingItems,ctx))}
        catch(error){if(error instanceof QueryError)throw new Problem(error.status,error.message,error.extra);throw error}
      }
      // A contact-form message needs no session and writes nothing here (`contact.ts`).
      if(request.method==='POST'&&path==='/api/contact')return await sendContact(env,request);
      // How many people each chat place has, read from the platforms and kept at the edge (`community.ts`).
      if(request.method==='GET'&&path==='/api/community')return await communityCounts(env,ctx,url);
      if(request.method==='POST'){
        // Every write is made by the user the request is signed in as; a browser starts an anonymous
        // session before its first one.
        const me=await viewer(env,request,true);
        if(!me)throw new Problem(401,'Sign in to save.');
        // An admin's page: rejecting what a reviewer saved. Banning and roles are Better Auth's own.
        if(path.startsWith('/api/admin/')&&!me.admin)throw new Problem(403,'Only an admin can do this.');
        if(path==='/api/admin/reject')return json(await reject(env,me.id,await body(request),{text}));
        if(path==='/api/admin/corpus-flags')return json(await flagCorpus(env,me.id,await body(request),{text}));
        if(path==='/api/account/avatar'){
          const origin=request.headers.get('origin');
          if(origin&&origin!==url.origin)throw new Problem(403,'Use the account page on this site.');
          const {status,body:out}=await setAvatar(env,me,q.get('source')??'',request);return json(out,status);
        }
        if(path==='/api/favourites'){
          try{return json(await setFavourite(env,me.id,await body(request),ctx))}
          catch(error){if(error instanceof FavouriteError)throw new Problem(error.status,error.message);if(error instanceof StaleCrop)return staleResponse();throw error}
        }
        if(path==='/api/account/claim'){const {status,body:out}=await claim(env,me,String((await body(request)).reviewer??''),request.headers.get('cf-connecting-ip'));return json(out,status)}
        const reviewed=await reviewRoute(env,request,path,me.id);
        if(reviewed)return reviewed;
        const claimed=await claimWrite(env,request,path,me,claimTools,async()=>{
          const {success}=await env.CLAIMS.limit({key:addressKey(request.headers.get('cf-connecting-ip'))});
          if(!success)throw new Problem(429,'Too many claims at once. Wait a minute and try again.')},ctx);
        if(claimed)return claimed instanceof Response?claimed:json(claimed);
        const formed=await formsRoute(env,request,path,q,formTools,ctx,me.id);
        if(formed)return formed instanceof Response?formed:json(formed);
        throw new Problem(404,'Unknown endpoint.');
      }
      if(!['GET','HEAD'].includes(request.method))throw new Problem(405,'Method not allowed.');
      if(path.startsWith('/api/admin/')){
        if(!(await viewer(env,request,true))?.admin)throw new Problem(403,'Only an admin can see this.');
        if(path==='/api/admin/reviewers')return json(await reviewers(env,q));
        if(path==='/api/admin/submissions')return json(await submissions(env,q,await reviewerActors(env,Object.fromEntries(q),{text})));
        throw new Problem(404,'Unknown endpoint.');
      }
      const picture=path.match(AVATAR_PATH);
      if(picture)return await avatar(env,picture[1],picture[2]);
      if(path==='/api/account/connections'){const me=await viewer(env,request,true);if(!me)throw new Problem(401,'Sign in to see your account.');return json(await connections(env,me.id))}
      if(path==='/api/favourites'||path==='/api/favourites/crops'){
        // A browser that has never signed in has starred nothing, and reading starts no session.
        const me=await viewer(env,request);
        if(path==='/api/favourites')return json(me?await favouriteIds(env,me.id):{ids:[]},200,{'cache-control':'private, no-store'});
        return json(me?await favouriteCrops(env,me.id,integer(q,'offset',0),integer(q,'limit',60,60),listingItems):{items:[],total:0,offset:0,next:null},200,{'cache-control':'private, no-store'});
      }
      if(path==='/api/account')return json({user:await viewer(env,request,true),providers:providers(env)});
      if(path==='/health')return json(await browse.health(env));
      const image=path.match(/^\/atlas\/media\/([a-f0-9]{64})\.webp$/);
      if(image){
        try{const found=await mediaResponse(env,request,image[1],ctx);if(found)return found}
        catch(error){if(error instanceof MediaIncomplete)throw new Problem(503,'Image publication is incomplete.');throw error}
        throw new Problem(404,'Image not found.');
      }
      // A round leaves out what its reviewer skipped lately, so it reads who is asking.
      if(path==='/atlas'){const timing=new Timing();const body=await browse.catalogue(env,ctx,url,q.get('purpose')==='review'?(await viewer(env,request))?.id??null:null,timing);
        return json(body,200,{'server-timing':timing.header()})}
      if(path==='/api/ranking')return await ranking(env,url,ctx);
      if(path==='/atlas/history')return json(await history(env,q,(await viewer(env,request))?.id??null,{text,integer}));
      const run=await runsRoute(env,ctx,url);
      if(run)return run;
      if(path==='/atlas/corpus/characters')return json(await browse.corpusCharacters(env,ctx,url),200,{'cache-control':'private, max-age=300'});
      const cropped=await cropRoute(env,ctx,url);
      if(cropped)return cropped;
      if(path==='/atlas/collection/status')return json(await browse.siteValue(env,'collection'));
      if(path==='/atlas/dates/stats')return json(await browse.dateStats(env,ctx,url));
      const read=await claimRead(env,url,claimTools,ctx);
      if(read)return read instanceof Response?read:json(read);
      if(path==='/atlas/reviews'||path==='/atlas/reviews.json')return json(await reviewsPage(env,q),200,
        path.endsWith('.json')?{'content-disposition':'attachment; filename="atlas-character-reviews.json"'}:{});
      const cited=path.match(/^\/atlas\/cite\/([^/]+)\/([^/]+)$/);
      if(cited&&(cited[1]==='grapheme'||cited[1]==='form'))return await citeCharacter(env,ctx,url,cited[1],cited[2],characterTools,OPEN);
      if(path==='/atlas/similar/model')return json(await modelInfo(env));
      if(path.startsWith('/atlas/ids/')){
        const drawing=await drawingFile(env,path.slice('/atlas/ids/'.length),request,ctx);
        if(!drawing)throw new Problem(404,'No drawing of this sequence.');
        return drawing;
      }
      if(path.startsWith('/atlas/composer/')){
        const file=await composerFile(env,path.slice('/atlas/composer/'.length),request,ctx);
        if(!file)throw new Problem(404,'No such composer file.');
        return file;
      }
      if(path.startsWith('/atlas/similar/files/')){
        const file=await modelFile(env,path.slice('/atlas/similar/files/'.length),request,ctx);
        if(!file)throw new Problem(404,'No such model file.');
        return file;
      }
      const similar=path.match(/^\/atlas\/characters\/([^/]+)\/similar$/);
      if(similar){const named=await cropParam(env,similar[1],ctx);
        if(named&&'moved' in named)return movedPathResponse(url,similar[1],named.moved);
        const crop=named&&('id' in named?named.id:named.retired);
        return json(await similarCrops(env,crop,integer(q,'limit',12,20),listingItems,ctx))}

      if(path==='/layers/word-choices')return json({items:await wordChoices(env,text(q.get('q')??'',128,'reading')??'',[...(q.get('c')??'')].slice(0,4))});
      if(path==='/layers/suggest')return json(await suggest(env,q,ctx));
      if(path==='/layers/structure')return json(await structureOf(env,ctx,url.origin,q.get('c')||''));
      if(path==='/layers/search'){const found=await suggest(env,q,ctx);return json({...found,results:found.items,match:found.items[0]||null})}
      const layer=path.match(/^\/layers\/characters\/([^/]+)$/);
      if(layer)return json(await characterPage(env,ctx,url,decodeURIComponent(layer[1]),characterTools));
      if(path==='/layers/occurrences')return json(await occurrences(env,await characterCard(env,q.get('code_point')||'',characterTools,ctx),q));
      if(path==='/layers/candidates'){const found=await corpusOccurrences(env,await characterCard(env,q.get('code_point')||'',characterTools,ctx),q);
        return json({...found,glyphs:found.total,glyph_items:found.items,retry:false})}
      if(path==='/layers/gallery'){const timing=new Timing();const body=await browse.gallery(env,ctx,q,timing);return json(body,200,{'server-timing':timing.header()})}
      if(path==='/layers/decades')return json(await decades(env,ctx,url,characterTools));
      if(path==='/layers/chronology')return json(await chronology(env,ctx,url,characterTools));
      if(path==='/layers/summary')return json(await browse.siteValue(env,'corpus_index'));
      if(path==='/layers/graphemes'||path==='/layers/ligatures')return json(await cards(env,path.endsWith('ligatures')?'ligatures':'graphemes',q,ctx));
      if(path==='/atlas/corpus/reviews')return json(await flaggedCorpus(env,listingItems));
      if(path.startsWith('/atlas/forms/')){const formed=await formsRoute(env,request,path,q,formTools,ctx);
        if(formed)return formed instanceof Response?formed:json(formed)}
      throw new Problem(404,'Unknown endpoint.');
    }catch(error){
      const open=opened(path)?OPEN:{};
      if(error instanceof StorageUnavailable)console.error(JSON.stringify({event:'storage_unavailable',path,where:error.where,...described(error.cause)}));
      if(error instanceof Problem)return json({detail:error.message,...error.extra},error.status,
        error.extra.code==='busy'?{...open,'retry-after':String(RETRY_AFTER)}:open);
      throw error;
    }
  },
};

// A writer reads the catalogue as it now stands: a write that succeeds marks the browser for as long as
// another visitor's answers may stand behind it, `CATALOGUE_LAG` seconds of stamps an isolate holds and
// the minute a kept listing stands for (`browse.KEPT_SECONDS`), and a marked request reads the stamps
// afresh, which the isolate then keeps.
const WROTE = 'atlas-wrote';
export const wroteLately = (cookie: string | null) => (cookie ?? '').split(';').some(part => part.trim() === `${WROTE}=1`);
export function markWriter(response: Response, lag: number, kept = 0): Response {
  if (!(lag > 0) || !response.ok) return response;
  const marked = new Response(response.body, response);
  marked.headers.append('set-cookie', `${WROTE}=1; Max-Age=${lag + kept}; Path=/; HttpOnly; Secure; SameSite=Lax`);
  return marked;
}
export default {
  async fetch(request:Request,env:Env,ctx:ExecutionContext):Promise<Response>{
    // A read is tried again while D1 is busy; a write is answered at once, and the browser keeps it
    // until the database is back.
    const read=request.method==='GET'||request.method==='HEAD';
    const scoped=wroteLately(request.headers.get('cookie'))?{...env,CATALOGUE_LAG:'0' as string} as Env:env;
    const outcome=await retried(()=>routes.fetch(request,scoped,ctx),read?READ_BUDGET:0);
    if('value' in outcome)return read?outcome.value:markWriter(outcome.value,Number(env.CATALOGUE_LAG),browse.KEPT_SECONDS);
    const path=new URL(request.url).pathname,open=opened(path)?OPEN:{};
    const busy=transient(outcome.error);
    console.error(JSON.stringify({event:'request_failed',path,method:request.method,busy,attempts:outcome.attempts,...described(outcome.error)}));
    if(busy)return json({detail:'The database is updating. Try again in a moment.',code:'busy'},503,{...open,'retry-after':String(RETRY_AFTER)});
    return json({detail:'The request could not be completed.',code:'failed'},500,open);
  },
} satisfies ExportedHandler<Env>;
