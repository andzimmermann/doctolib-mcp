import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {Miniflare,Response as MFResponse} from 'miniflare';
const TOKEN='fake_client_token_for_offline_tests_only_1234567890';
const fixture=async name=>JSON.parse(await readFile(new URL('../tests/fixtures/'+name+'.json',import.meta.url),'utf8'));
const origin='https://mcp.example.test';
async function instance(t,{cookie=true,confirmationFails=false,commitFails=false,changePatient=false,keepAlive=false,patientResponse=null}={}){
 const people=await fixture('master_patients'),profile=await fixture('practitioner_info'),form=await fixture('patient_form');
 const tomorrow=new Date(Date.now()+86400000).toISOString().slice(0,10),slot=tomorrow+'T10:00:00+01:00';const calls=[];
 const vars={DEPLOYMENT_OWNER:'changepoint.eu',PUBLIC_ORIGIN:origin,DIRECT_MODE:'true',ENABLE_WRITES:'true',SESSION_KEEP_ALIVE:String(keepAlive),SESSION_KEY:'ab'.repeat(32),MCP_TOKEN_SHA256:createHash('sha256').update(TOKEN).digest('hex'),...(cookie?{DOCTOLIB_COOKIE:'session=FAKE_TEST_ONLY'}:{})};
 const env=Object.fromEntries(Object.entries(vars).map(([k,value])=>[k,{type:'text',value}]));env.ACCOUNT_STATE={type:'durable-object',worker:'direct-test',exportName:'DoctolibAccount'};
 const options={cf:false,enableDevRegistry:false,logRequests:false,telemetry:{enabled:false},workers:[{config:{name:'direct-test',compatibilityDate:'2026-11-08',compatibilityFlags:['nodejs_compat','global_fetch_strictly_public'],exports:{DoctolibAccount:{type:'durable-object',storage:'sqlite'}},env,manifest:{mainModule:'worker.mjs',modules:{'worker.mjs':{type:'esm',contents:await readFile(new URL('../cloudflare/dist-worker/worker.js',import.meta.url),'utf8')}}}},dev:{outboundService:{type:'fetcher',async handler(r){
  const u=new URL(r.url);assert.equal(u.hostname,'www.doctolib.de');assert.equal(r.headers.get('authorization'),null);assert.equal(r.headers.get('cookie'),'session=FAKE_TEST_ONLY');calls.push({path:u.pathname,query:Object.fromEntries(u.searchParams),method:r.method,body:r.method==='POST'||r.method==='DELETE'?await r.json():null});
  if(u.pathname==='/account/master_patients.json')return patientResponse?patientResponse(people,r):MFResponse.json(people);
  if(u.pathname.includes('slot_selection_funnel/v1/info'))return MFResponse.json(profile);
  if(u.pathname==='/availabilities.json')return MFResponse.json({total:1,availabilities:[{date:tomorrow,slots:[{start_date:slot}]}]});
  if(u.pathname==='/account/appointments')return new MFResponse('<meta name="csrf-token" content="FAKE_CSRF">',{headers:{'content-type':'text/html'}});
  if(u.pathname.includes('checkout/v1/patient_form'))return MFResponse.json(changePatient?{...form,patient:{...form.patient,uuid:'someone-else'}}:form);
  if(u.pathname.includes('patient_confirm'))return MFResponse.json(commitFails?{}:{id:'mock-booking'},{status:commitFails?500:200});
  if(u.pathname.includes('confirmation/v1/booking'))return MFResponse.json({}, {status:confirmationFails?500:200});
  if(r.method==='POST'||r.method==='DELETE'){assert.equal(r.headers.get('x-csrf-token'),'FAKE_CSRF');return MFResponse.json({ok:true});}
  assert.fail('Unexpected mock path '+u.pathname);
 }}}}]};
 const mf=new Miniflare(options);t.after(()=>mf.dispose());await mf.ready;
 const rpc=async(method,params={})=>{const r=await mf.dispatchFetch(origin+'/mcp',{method:'POST',headers:{authorization:'Bearer '+TOKEN,'content-type':'application/json',accept:'application/json, text/event-stream','mcp-protocol-version':'2025-11-25'},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})});const text=await r.text();assert.equal(r.status,200,text);return JSON.parse(text);};
 const tool=async(name,args={})=>{const r=await rpc('tools/call',{name,arguments:args});assert.ok(r.result,JSON.stringify(r));return {...JSON.parse(r.result.content[0].text),isError:r.result.isError};};
 const ns=await mf.getDurableObjectNamespace('ACCOUNT_STATE');const stub=ns.get(ns.idFromName('personal-de'));
 return {mf,options,rpc,tool,calls,people,slot,tomorrow,stub};
}
const bookArgs=(x)=>({start_date:x.slot,profile_id:554433,visit_motive_ids:[55012],agenda_ids:[101],practice_ids:[901],patient_id:x.people[1].uuid,new_patient:false,confirm:true});
test('direct Worker initializes and lists fifteen tools without a Doctolib session',async t=>{const x=await instance(t,{cookie:false});const init=await x.rpc('initialize',{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'test',version:'1'}});assert.ok(init.result.serverInfo);const listed=await x.rpc('tools/list');assert.equal(listed.result.tools.length,15);assert.ok(listed.result.tools.every(t=>!t.inputSchema.properties.cookie));const p=await x.tool('list_patients');assert.equal(p.error,'DOCTOLIB_SESSION_REQUIRED');assert.equal(x.calls.length,0);});
test('exact family member is chosen and booking is deduplicated',async t=>{const x=await instance(t);const result=await x.tool('book_appointment',bookArgs(x));assert.equal(result.booked,true,JSON.stringify(result));const choice=x.calls.find(c=>c.path.includes('patient_selection/v1/select'));assert.equal(choice.body.master_patient_id,x.people[1].signed_id);assert.equal((await x.tool('book_appointment',bookArgs(x))).deduplicated,true);assert.equal(x.calls.filter(c=>c.path.includes('patient_confirm')).length,1);});
test('unknown commit does not retry and confirmation failure retains committed status',async t=>{const x=await instance(t,{commitFails:true});assert.equal((await x.tool('book_appointment',bookArgs(x))).booked,null);await x.tool('book_appointment',bookArgs(x));assert.equal(x.calls.filter(c=>c.path.includes('patient_confirm')).length,1);const y=await instance(t,{confirmationFails:true});assert.equal((await y.tool('book_appointment',bookArgs(y))).booked,true);assert.equal(y.calls.filter(c=>c.path==='/online_booking/draft.json').length,0);});
test('changed person in checkout cannot commit a booking',async t=>{const x=await instance(t,{changePatient:true});const result=await x.tool('book_appointment',bookArgs(x));assert.equal(result.booked,false);assert.equal(x.calls.filter(c=>c.path.includes('patient_confirm')).length,0);});
test('manual preview performs no network requests',async t=>{const x=await instance(t);const result=await x.tool('book_appointment',{...bookArgs(x),confirm:false});assert.equal(result.preview,true);assert.equal(x.calls.length,0);});
test('persistent monitor runs exact criteria once and stops after success',async t=>{const x=await instance(t);const rule={profile_slug:'dr-anna-mueller',profile_id:554433,visit_motive_id:55012,agenda_id:101,practice_id:901,patient_id:x.people[2].uuid,new_patient:false,date_from:x.tomorrow,date_to:x.tomorrow,time_from:'09:00',time_to:'17:00',weekdays:[0,1,2,3,4,5,6],timezone:'Europe/Berlin',interval_seconds:300,auto_book:true,insurance_sector:'private'};const created=await x.tool('create_appointment_monitor',rule);assert.equal(created.state,'active',JSON.stringify(created));await x.stub.tick();assert.equal(x.calls.find(c=>c.path==='/availabilities.json').query.insurance_sector,'private');assert.equal((await x.tool('list_appointment_monitors')).monitors[0].state,'booked');await x.stub.tick();assert.equal(x.calls.filter(c=>c.path.includes('patient_confirm')).length,1);});
test('duplicate manual booking reservation survives Worker restart',async t=>{const x=await instance(t);await x.tool('book_appointment',bookArgs(x));const w=x.options.workers[0];await x.mf.setOptions({...x.options,workers:[{...w,config:{...w.config,manifest:{mainModule:'worker.mjs',modules:{'worker.mjs':{type:'esm',contents:(await readFile(new URL('../cloudflare/dist-worker/worker.js',import.meta.url),'utf8'))+'\n// restart test'}}}}}]});const again=await x.tool('book_appointment',bookArgs(x));assert.equal(again.deduplicated,true);assert.equal(x.calls.filter(c=>c.path.includes('patient_confirm')).length,1);});
test('mobile connection page requires a token once and protects every browser action',async t=>{
 const x=await instance(t,{cookie:false});
 const page=await x.mf.dispatchFetch(origin+'/connect');assert.equal(page.status,200);assert.ok((await page.text()).includes('Sitzung übernehmen'));assert.ok(page.headers.get('content-security-policy').includes("frame-ancestors 'none'"));
 assert.equal((await x.mf.dispatchFetch(origin+'/connect/status')).status,401);
 const unlock=await x.mf.dispatchFetch(origin+'/connect/unlock',{method:'POST',headers:{origin,authorization:'Bearer '+TOKEN}});assert.equal(unlock.status,200);
 const cookie=unlock.headers.get('set-cookie');assert.ok(cookie.includes('HttpOnly'));assert.ok(cookie.includes('SameSite=Strict'));assert.ok(!cookie.includes(TOKEN));
 const status=await x.mf.dispatchFetch(origin+'/connect/status',{headers:{cookie}});assert.equal((await status.json()).available,false);
 for(const bad of ['', 'https://evil.example.test'])assert.equal((await x.mf.dispatchFetch(origin+'/connect/start',{method:'POST',headers:{cookie,origin:bad,'content-type':'application/json'},body:'{}'})).status,403);
 const noBrowser=await x.mf.dispatchFetch(origin+'/connect/start',{method:'POST',headers:{cookie,origin,'content-type':'application/json'},body:'{}'});assert.equal((await noBrowser.json()).error,'BROWSER_SETUP_REQUIRED');
 // A setup-only cookie never grants MCP/account access.
 assert.equal((await x.mf.dispatchFetch(origin+'/mcp',{method:'POST',headers:{cookie,'content-type':'application/json'},body:'{}'})).status,401);
 assert.equal(x.calls.length,0);
 const reauth=await x.tool('session_status');assert.equal(reauth.reconnect_url,origin+'/connect');assert.ok(!JSON.stringify(reauth).includes(TOKEN));
});
test('validated login can be imported without a Worker cookie secret and is never returned',async t=>{const x=await instance(t,{cookie:false});const r=await x.mf.dispatchFetch(origin+'/setup/session',{method:'POST',headers:{authorization:'Bearer '+TOKEN,'content-type':'application/json'},body:JSON.stringify({cookie:'session=FAKE_TEST_ONLY'})});assert.equal(r.status,200,await r.clone().text());assert.deepEqual(await r.json(),{ok:true});assert.equal((await x.tool('session_status')).logged_in,true);});
test('session validation rejects JSON that is not an authenticated patient response',async t=>{const {DocAPI}=await import('../cloudflare/direct-api.mjs');t.mock.method(globalThis,'fetch',async()=>new Response('{}',{headers:{'content-type':'application/json'}}));await assert.rejects(()=>new DocAPI('session=FAKE_TEST_ONLY').patients(),/INVALID_PATIENT_RESPONSE/);});
test('a failed patient preflight never reserves or sends a write',async t=>{const x=await instance(t);const args={...bookArgs(x),patient_id:'not-on-account'};for(let i=0;i<2;i++){const r=await x.tool('book_appointment',args);assert.equal(r.error,'EXACT_PATIENT_NOT_FOUND');}assert.equal(x.calls.filter(c=>c.method==='POST').length,0);});

test('availability requests forward private insurance through MCP and chunked dates',async t=>{const x=await instance(t);await x.tool('get_availabilities',{visit_motive_id:55012,agenda_ids:[101],practice_id:901,insurance_sector:'private',start_date:x.tomorrow,days:16});const calls=x.calls.filter(c=>c.path==='/availabilities.json');assert.equal(calls.length,2);assert.ok(calls.every(c=>c.query.insurance_sector==='private'));assert.notEqual(calls[0].query.start_date,calls[1].query.start_date);});
test('selected person supplies insurance automatically without a filter argument',async t=>{const x=await instance(t);x.people[1].insurance_sector='privat';const r=await x.tool('get_availabilities',{patient_id:x.people[1].uuid,visit_motive_id:55012,agenda_ids:[101],practice_id:901,start_date:x.tomorrow,days:14});assert.ok(!r.isError,JSON.stringify(r));assert.equal(x.calls.find(c=>c.path==='/availabilities.json').query.insurance_sector,'private');});
test('insurance stays person-specific and unknown status is not guessed',async t=>{const x=await instance(t);x.people[1].insurance_sector='privat';delete x.people[2].insurance_sector;const args={visit_motive_id:55012,agenda_ids:[101],practice_id:901,start_date:x.tomorrow,days:14};const missing=await x.tool('get_availabilities',{...args,patient_id:x.people[2].uuid});assert.equal(missing.error,'PATIENT_INSURANCE_REQUIRED');assert.equal(x.calls.filter(c=>c.path==='/availabilities.json').length,0);await x.tool('get_availabilities',{...args,patient_id:x.people[0].uuid});assert.equal(x.calls.find(c=>c.path==='/availabilities.json').query.insurance_sector,'public');});

// Daily background checks are independent of appointment searches and never write bookings.
test('daily session check runs once without monitors and retains schedule across restart',async t=>{
 const x=await instance(t,{keepAlive:true});await x.stub.tick();await x.stub.tick();
 assert.deepEqual(x.calls.map(c=>c.path),['/account/master_patients.json']);
 const state=JSON.parse(await x.stub.get('session_keep_alive'));
 assert.equal(state.next_check-state.last_attempt,86400000);assert.ok(state.last_success);assert.equal(state.paused,false);
 const w=x.options.workers[0];await x.mf.setOptions({...x.options,workers:[{...w,config:{...w.config,manifest:{mainModule:'worker.mjs',modules:{'worker.mjs':{type:'esm',contents:(await readFile(new URL('../cloudflare/dist-worker/worker.js',import.meta.url),'utf8'))+'\n// daily restart test'}}}}}]});
 const ns=await x.mf.getDurableObjectNamespace('ACCOUNT_STATE');x.stub=ns.get(ns.idFromName('personal-de'));
 await x.stub.tick();assert.equal(x.calls.length,1);
 await x.stub.set('session_keep_alive',JSON.stringify({...state,next_check:0}));await x.stub.tick();assert.equal(x.calls.length,2);
 const status=await x.tool('session_status');assert.equal(status.keep_alive.enabled,true);assert.equal(status.keep_alive.interval_seconds,86400);
});
test('daily checks pause on revoked sessions and resume only after a valid import',async t=>{
 for(const status of [401,403]){
  let expired=true;const x=await instance(t,{keepAlive:true,patientResponse:people=>expired?MFResponse.json({}, {status}):MFResponse.json(people)});
  await x.stub.tick();await x.stub.tick();assert.equal(x.calls.length,1);
  const state=JSON.parse(await x.stub.get('session_keep_alive'));assert.equal(state.paused,true);assert.equal(state.next_check,null);
  expired=false;const imported=await x.stub.importSession('session=FAKE_TEST_ONLY');assert.equal(imported.ok,true);
  const resumed=JSON.parse(await x.stub.get('session_keep_alive'));assert.equal(resumed.paused,false);assert.ok(resumed.next_check>Date.now());
  await x.stub.tick();assert.equal(x.calls.length,2);
 }
});
test('daily transient failures wait a day and never trigger rapid retries',async t=>{
 const x=await instance(t,{keepAlive:true,patientResponse:()=>MFResponse.json({}, {status:500})});await x.stub.tick();await x.stub.tick();
 assert.equal(x.calls.length,1);const state=JSON.parse(await x.stub.get('session_keep_alive'));
 assert.equal(state.paused,false);assert.equal(state.last_success,null);assert.equal(state.error,'DOCTOLIB_HTTP_500');assert.equal(state.next_check-state.last_attempt,86400000);
});
test('disabled or missing-session keep-alive makes no requests',async t=>{
 for(const options of [{keepAlive:false},{keepAlive:true,cookie:false}]){const x=await instance(t,options);await x.stub.tick();assert.equal(x.calls.length,0);}
});
test('daily check saves rotated cookies encrypted for subsequent requests',async t=>{
 let n=0;const x=await instance(t,{keepAlive:true,patientResponse:people=>MFResponse.json(people,{headers:++n===1?{'set-cookie':'renewed=FAKE_ROTATED; Path=/; Secure; HttpOnly'}:{}})});
 await x.stub.tick();const stored=await x.stub.get('session');assert.ok(stored);assert.ok(!stored.includes('FAKE_ROTATED'));
 const encrypted=JSON.parse(stored),key=await crypto.subtle.importKey('raw',Uint8Array.from({length:32},()=>0xab),'AES-GCM',false,['decrypt']);
 const plain=new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:Uint8Array.from(encrypted.iv)},key,Uint8Array.from(encrypted.value)));
 assert.ok(plain.includes('renewed=FAKE_ROTATED'));assert.ok(x.calls.every(c=>c.method==='GET'));
});
