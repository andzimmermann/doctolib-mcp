import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {Miniflare,Response as MFResponse} from 'miniflare';
const TOKEN='fake_client_token_for_offline_tests_only_1234567890';
const SECRET='fake_backend_token_for_offline_tests_only_1234567890';
const vars={DEPLOYMENT_OWNER:'changepoint.eu',PUBLIC_ORIGIN:'https://mcp.example.test',BACKEND_URL:'https://backend.example.test/mcp',BACKEND_HOST:'backend.example.test',BACKEND_TOKEN:SECRET,MCP_TOKEN_SHA256:createHash('sha256').update(TOKEN).digest('hex')};
async function instance(t,overrides={},outbound=()=>MFResponse.json({ok:true})){
 const mf=new Miniflare({cf:false,enableDevRegistry:false,logRequests:false,telemetry:{enabled:false},workers:[{config:{name:'gateway-test',compatibilityDate:'2026-11-08',compatibilityFlags:['global_fetch_strictly_public'],env:Object.fromEntries(Object.entries({...vars,...overrides}).map(([key,value])=>[key,{type:'text',value}])),manifest:{mainModule:'worker.mjs',modules:{'worker.mjs':{type:'esm',contents:await readFile(new URL('../cloudflare/worker.mjs',import.meta.url),'utf8')}}}},dev:{outboundService:{type:'fetcher',handler:outbound}}}]});
 t.after(()=>mf.dispose());await mf.ready;return mf;
}
function req(mf,extra={}){return mf.dispatchFetch('https://mcp.example.test/mcp',{method:'POST',headers:{authorization:'Bearer '+TOKEN,'content-type':'application/json',...extra},body:'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'});}
test('missing and wrong token fail before backend access',async t=>{let calls=0;const mf=await instance(t,{},()=>{calls++;return MFResponse.json({});});for(const authorization of ['', 'Bearer invalid', 'Bearer '+TOKEN+'x'])assert.equal((await req(mf,{authorization})).status,401);assert.equal(calls,0);});
test('valid access replaces authorization and strips cookies and foreign headers',async t=>{let seen;const mf=await instance(t,{},async r=>{seen={url:r.url,auth:r.headers.get('authorization'),cookie:r.headers.get('cookie'),foreign:r.headers.get('x-private'),body:await r.text()};return MFResponse.json({ok:true},{headers:{'set-cookie':'not-forwarded=1'}});});const r=await req(mf,{cookie:'do_not_forward=1','x-private':'private'});assert.equal(r.status,200);assert.deepEqual({...seen,body:undefined},{url:vars.BACKEND_URL,auth:'Bearer '+SECRET,cookie:null,foreign:null,body:undefined});assert.ok(seen.body.includes('tools/list'));assert.equal(r.headers.get('set-cookie'),null);assert.equal(r.headers.get('cache-control'),'no-store');});
test('setup and owner errors fail closed',async t=>{const mf=await instance(t,{DEPLOYMENT_OWNER:'mysupply.ai'});assert.equal((await req(mf)).status,503);});
test('browser cross-origin calls are rejected',async t=>{const mf=await instance(t);assert.equal((await req(mf,{origin:'https://other.example.test'})).status,403);});
test('oversized authenticated requests are rejected',async t=>{const mf=await instance(t);const r=await mf.dispatchFetch('https://mcp.example.test/mcp',{method:'POST',headers:{authorization:'Bearer '+TOKEN},body:'x'.repeat(524289)});assert.equal(r.status,413);});
test('backend redirect is never followed or retried',async t=>{let calls=0;const mf=await instance(t,{},()=>{calls++;return new MFResponse(null,{status:302,headers:{location:'https://other.example.test'}});});assert.equal((await req(mf)).status,502);assert.equal(calls,1);});
test('unknown write outcome never retries backend failures',async t=>{let calls=0;const mf=await instance(t,{},()=>{calls++;return new MFResponse('secret upstream error',{status:500});});const r=await req(mf);assert.equal(r.status,502);assert.ok((await r.text()).includes('OUTCOME_UNKNOWN'));assert.equal(calls,1);});
