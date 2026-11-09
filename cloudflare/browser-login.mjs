import puppeteer from '@cloudflare/puppeteer';
import {ApiError} from './direct-api.mjs';
import {loginFrame,loginInteraction} from './login-input.mjs';

const lifetime=20*60*1000;
const policyVersion=2;
const allowedDomains=['doctolib.de','*.doctolib.de','doctolib.com','*.doctolib.com','assets.doctolib.fr'];
function state(account){return JSON.parse(account.get('browser_login')||'null');}
function forget(account){account.ctx.storage.sql.exec('DELETE FROM meta WHERE key=?','browser_login');}
export function loginCheckpoint(account,action,stage,error=null){account.set('browser_login_result',JSON.stringify({action,stage,error,at:Date.now()}));}
function browserError(error){
 const message=String(error?.message||'');
 if(/daily|browser time|time limit|quota/i.test(message))return 'BROWSER_TIME_LIMIT';
 if(/concurrent|available browser|browser limit/i.test(message))return 'BROWSER_CONCURRENCY_LIMIT';
 if(/rate limit|code: 429|status.?429/i.test(message))return 'BROWSER_RATE_LIMIT';
 if(/ERR_ABORTED/i.test(message))return 'BROWSER_NAVIGATION_INTERRUPTED';
 if(/ERR_BLOCKED|not-in-allowlist/i.test(message))return 'BROWSER_RESOURCE_BLOCKED';
 if(/timeout|timed out/i.test(message))return 'BROWSER_TIMEOUT';
 if(/not a function/i.test(message))return 'BROWSER_SDK_METHOD_UNAVAILABLE';
 const status=message.match(/(?:code|status|HTTP)[:\s]+(\d{3})/i)?.[1];
 return status?'BROWSER_HTTP_'+status:'LOGIN_FAILED';
}
async function view(browser,page){
 const cdp=await page.createCDPSession();
 try{
  const result=await cdp.send('Cloudflare.getLiveView',{mode:'tab',expiresInMs:300000});
  const u=new URL(result.devtoolsFrontendUrl);
  if(u.protocol!=='https:'||u.hostname!=='live.browser.run')throw new ApiError('LOGIN_VIEW_INVALID');
  return u.href;
 }finally{await cdp.detach();}
}
export async function browserLogin(account,action,id,driver=puppeteer,interaction){
 let current=state(account);
 if(action==='status'){
  let browser_limits=null;
  if(account.env.BROWSER&&driver.limits)try{const limits=await driver.limits(account.env.BROWSER);browser_limits={active_sessions:limits.activeSessions.length,max_concurrent:limits.maxConcurrentSessions,allowed_acquisitions:limits.allowedBrowserAcquisitions,time_until_next_acquisition:limits.timeUntilNextAllowedBrowserAcquisition};}catch{}
  return {available:!!account.env.BROWSER,pending:!!current&&current.expires_at>Date.now(),login_id:current?.id,expires_at:current?.expires_at,last_result:JSON.parse(account.get('browser_login_result')||'null'),browser_limits,launch_error:account.get('browser_launch_error')||null,retry_at:Number(account.get('browser_retry_at'))||null};
 }
 if(!account.env.BROWSER)throw new ApiError('BROWSER_SETUP_REQUIRED',503);
 if(!['start','finish','cancel','expire','frame','interact'].includes(action))throw new ApiError('LOGIN_ACTION_INVALID');
 if(action==='start'&&!current&&Number(account.get('browser_retry_at'))>Date.now()){loginCheckpoint(account,action,'rate_limit');throw new ApiError('BROWSER_RATE_LIMIT',429);}
 if(action==='expire'&&(!current||current.expires_at>Date.now()))return {expired:false};
 if(action!=='start'&&action!=='expire'&&(!current||id!==current.id))throw new ApiError('LOGIN_NOT_FOUND',404);
 // Browser Run policies are fixed at launch. Replace a window with an obsolete asset policy.
 if(action==='start'&&current&&current.policy_version!==policyVersion){
  try{const browser=await driver.connect(account.env.BROWSER,current.session_id);await browser.close();}catch{}
  forget(account);current=null;
 }
 if(current&&current.expires_at<=Date.now()||action==='cancel'||action==='expire'){
  if(current){
   try{const browser=await driver.connect(account.env.BROWSER,current.session_id);await browser.close();}catch{}
   forget(account);current=null;
  }
  if(action==='cancel'||action==='expire')return {ok:true};
  if(action==='finish')throw new ApiError('LOGIN_EXPIRED',410);
 }
 let browser,close=false;
 try{
  loginCheckpoint(account,action,'browser_connection');
  if(current){
   try{browser=await driver.connect(account.env.BROWSER,current.session_id);}catch{forget(account);throw new ApiError('LOGIN_EXPIRED',410);}
  }else{
   loginCheckpoint(account,action,'launch_browser');
   const endpoint={fetch:async(...args)=>{const response=await account.env.BROWSER.fetch(...args);if(response.status===429){const raw=response.headers.get('retry-after');const seconds=raw&&/^\d+$/.test(raw)?Number(raw):null;const date=raw&&!seconds?Date.parse(raw):NaN;const retryAt=Number.isFinite(date)?date:Date.now()+(seconds??60)*1000;account.set('browser_retry_at',String(Math.max(Date.now()+1000,retryAt)));}return response;}};
   try{browser=await driver.launch(endpoint,{keep_alive:180000,guardrails:{allowedDomains,allowedDomainSets:['common-cdns']}});account.set('browser_launch_error','');account.set('browser_retry_at','');}
   catch(e){
    // This failure occurs before navigation or credential entry. Keep only redacted service text.
    const reason=String(e.message||'').replace(/https?:\/\/\S+/g,'[url]').replace(/[A-Za-z0-9_-]{24,}/g,'[redacted]').slice(0,500);
    account.set('browser_launch_error',reason);throw e;
   }
   close=true;
   current={id:crypto.randomUUID(),session_id:browser.sessionId(),expires_at:Date.now()+lifetime,policy_version:policyVersion};
   // Persist before navigation so errors/restarts cannot orphan an untracked browser.
   account.set('browser_login',JSON.stringify(current));await account.ctx.storage.sync();
   const pages=await browser.pages();const page=pages[0]||await browser.newPage();
   await page.setViewport({width:390,height:780,deviceScaleFactor:1});
   loginCheckpoint(account,action,'load_login_page');
   const response=await page.goto('https://www.doctolib.de/account/appointments',{waitUntil:'domcontentloaded',timeout:30000});
   if(response&&[403,429].includes(response.status()))throw new ApiError('DOCTOLIB_LOGIN_ACCESS_DENIED',response.status());
  }
  const pages=await browser.pages();const page=pages.find(p=>{try{const u=new URL(p.url());return u.protocol==='https:'&&['doctolib.de','doctolib.com'].some(h=>u.hostname===h||u.hostname.endsWith('.'+h));}catch{return false;}});
  if(!page)throw new ApiError('DOCTOLIB_LOGIN_PAGE_REQUIRED');
  if(action==='frame'||action==='interact'){
   await page.setViewport({width:390,height:780,deviceScaleFactor:1});
   // Input remains transient: neither the payload nor screenshots enter SQL, logs or MCP.
   return await (action==='frame'?loginFrame(page):loginInteraction(page,interaction));
  }
  if(action==='finish'){
   loginCheckpoint(account,action,'read_session');
   // This Cloudflare Puppeteer version exposes URL-scoped cookies on Page, not BrowserContext.
   const cookies=await page.cookies('https://www.doctolib.de/account/master_patients.json');
   const cookie=cookies.filter(c=>['doctolib.de','www.doctolib.de'].includes(c.domain.replace(/^\./,''))&&(!c.expires||c.expires<0||c.expires*1000>Date.now())&&('/account/master_patients.json'.startsWith(c.path||'/'))).map(c=>c.name+'='+c.value).join('; ');
   if(!cookie)throw new ApiError('DOCTOLIB_LOGIN_INCOMPLETE',401);
   loginCheckpoint(account,action,'validate_session');
   const imported=await account.importSession(cookie);
   if(!imported.ok)throw new ApiError(imported.error==='DOCTOLIB_REAUTHENTICATION_REQUIRED'?'DOCTOLIB_LOGIN_INCOMPLETE':imported.error||'SESSION_IMPORT_FAILED');
   close=true;forget(account);loginCheckpoint(account,action,'connected');return {ok:true,connected:true};
  }
  loginCheckpoint(account,action,'create_login_link');
  const login_url=await view(browser,page);close=false;loginCheckpoint(account,action,'awaiting_login');
  return {login_id:current.id,expires_at:current.expires_at,login_url};
 }catch(e){if(close)forget(account);if(e.code)throw e;throw new ApiError(browserError(e));}
 finally{if(browser){if(close)await browser.close().catch(()=>{});else await browser.disconnect().catch(()=>{});}}
}
