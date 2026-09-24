import Browser from "./browser.mjs";
import {writeFileSync} from "node:fs";
const browser=await Browser.launch({width:1440,height:1000});
try {
 await browser.send("Network.enable");
 await browser.send("Page.navigate",{url:"http://127.0.0.1:8770/"});
 await browser.waitFor("document.querySelectorAll(.glyph-tile).length > 0".replace(".glyph-tile","\".glyph-tile\""),45000);
 await new Promise(r=>setTimeout(r,12000));
 const result=await browser.evaluate(`({images:[...document.images].map(i=>({url:i.currentSrc,loaded:i.complete&&i.naturalWidth>0,width:i.naturalWidth,height:i.naturalHeight})),requests:performance.getEntriesByType("resource").map(r=>({url:r.name,type:r.initiatorType,start:Math.round(r.startTime),ms:Math.round(r.duration),ttfb:Math.round(r.responseStart-r.requestStart),bytes:r.transferSize}))})`);
 writeFileSync(process.argv[2]||"../../work/crop-performance/browser-before.json",JSON.stringify(result,null,2));
 const img=result.requests.filter(r=>r.type==="img");
 console.log(JSON.stringify({images:result.images.length,loaded:result.images.filter(i=>i.loaded).length,slowImages:img.sort((a,b)=>b.ms-a.ms).slice(0,6),api:result.requests.filter(r=>r.type==="fetch").map(r=>({url:r.url,ms:r.ms}))}));
} finally {await browser.close();}
