import {test} from 'node:test';
import assert from 'node:assert/strict';
import {browserLogin} from '../cloudflare/browser-login.mjs';
import {connectCookie,hasConnectCookie} from '../cloudflare/connect-auth.mjs';

function instance({status=200,importOk=true}={}){
 const data=new Map(),calls=[];let imported;
 const page={url:()=> 'https://www.doctolib.de/sessions/new',setViewport:async()=>{},goto:async url=>{calls.push(['goto',url]);return {status:()=>status};},createCDPSession:async()=>({send:async(method,args)=>{assert.equal(method,'Cloudflare.getLiveView');assert.equal(args.mode,'tab');return {devtoolsFrontendUrl:'https://live.browser.run/ui/view?jwt=FAKE_ONLY'};},detach:async()=>{}})};
 const cookies=[{name:'session',value:'FAKE_SESSION',domain:'.doctolib.de',path:'/',expires:-1},{name:'bad',value:'FOREIGN',domain:'evildoctolib.de',path:'/'},{name:'bad2',value:'WRONG_PATH',domain:'.doctolib.de',path:'/other'},{name:'bad3',value:'EXPIRED',domain:'.doctolib.de',path:'/',expires:1}];
 const browser={sessionId:()=> 'FAKE_BROWSER_ID',pages:async()=>[page],disconnect:async()=>calls.push(['disconnect']),close:async()=>calls.push(['close']),defaultBrowserContext:()=>({cookies:async()=>cookies})};
 const account={env:{BROWSER:{}},get:k=>data.get(k),set:(k,v)=>data.set(k,v),ctx:{storage:{sync:async()=>{},sql:{exec:(query,key)=>data.delete(key)}}},importSession:async cookie=>{imported=cookie;return importOk?{ok:true}:{ok:false,error:'DOCTOLIB_REAUTHENTICATION_REQUIRED'};}};
 const driver={launch:async(binding,options)=>{calls.push(['launch',options]);return browser;},connect:async(binding,id)=>{assert.equal(id,'FAKE_BROWSER_ID');calls.push(['connect']);return browser;}};
 return {account,driver,calls,data,cookies,get imported(){return imported;}};
}
test('iPhone handoff reuses the exact guarded browser and imports no foreign cookies',async()=>{
 const x=instance(),start=await browserLogin(x.account,'start',undefined,x.driver);
 assert.ok(start.login_url.startsWith('https://live.browser.run/'));assert.ok(!JSON.stringify(start).includes('FAKE_BROWSER_ID'));
 assert.ok(x.calls[0][1].guardrails.allowedDomains.includes('*.doctolib.de'));assert.equal(x.calls.filter(c=>c[0]==='close').length,0);
 const again=await browserLogin(x.account,'start',undefined,x.driver);assert.equal(again.login_id,start.login_id);assert.equal(x.calls.filter(c=>c[0]==='launch').length,1);
 const result=await browserLogin(x.account,'finish',start.login_id,x.driver);assert.deepEqual(result,{ok:true,connected:true});assert.equal(x.imported,'session=FAKE_SESSION');assert.equal(x.data.has('browser_login'),false);assert.equal(x.calls.filter(c=>c[0]==='close').length,1);assert.ok(!JSON.stringify(result).includes('FAKE_SESSION'));
});
test('unfinished login preserves the browser; cancellation and expiry close it',async()=>{
 const x=instance({importOk:false}),start=await browserLogin(x.account,'start',undefined,x.driver);
 await assert.rejects(()=>browserLogin(x.account,'finish',start.login_id,x.driver),/DOCTOLIB_LOGIN_INCOMPLETE/);assert.ok(x.data.has('browser_login'));
 await assert.rejects(()=>browserLogin(x.account,'finish','wrong-id',x.driver),/LOGIN_NOT_FOUND/);
 await browserLogin(x.account,'cancel',start.login_id,x.driver);assert.equal(x.data.has('browser_login'),false);
 const y=instance();await browserLogin(y.account,'start',undefined,y.driver);const state=JSON.parse(y.data.get('browser_login'));y.data.set('browser_login',JSON.stringify({...state,expires_at:Date.now()-1}));
 await browserLogin(y.account,'expire',undefined,y.driver);assert.equal(y.data.has('browser_login'),false);assert.equal(y.calls.filter(c=>c[0]==='close').length,1);
});
test('Doctolib access denial ends the browser without evasion or retry',async()=>{
 const x=instance({status:403});await assert.rejects(()=>browserLogin(x.account,'start',undefined,x.driver),/DOCTOLIB_LOGIN_ACCESS_DENIED/);
 assert.equal(x.calls.filter(c=>c[0]==='launch').length,1);assert.equal(x.calls.filter(c=>c[0]==='goto').length,1);assert.equal(x.calls.filter(c=>c[0]==='close').length,1);assert.equal(x.data.has('browser_login'),false);
});
test('mobile setup cookie rejects tampering, expiry and a rotated MCP credential',async()=>{
 const env={SESSION_KEY:'ab'.repeat(32),MCP_TOKEN_SHA256:'cd'.repeat(32)},value=await connectCookie(env);
 const req=cookie=>new Request('https://mcp.example.test/connect/status',{headers:{cookie}});
 assert.equal(await hasConnectCookie(req(value),env),true);assert.equal(await hasConnectCookie(req(value.replace('connect=','connect=1')),env),false);
 assert.equal(await hasConnectCookie(req(value),{...env,MCP_TOKEN_SHA256:'ef'.repeat(32)}),false);
 const expired=value.replace(/=\d{13}/,'='+String(Date.now()-1));assert.equal(await hasConnectCookie(req(expired),env),false);
});
