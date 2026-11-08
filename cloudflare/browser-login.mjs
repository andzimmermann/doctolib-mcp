import puppeteer from '@cloudflare/puppeteer';
import {ApiError} from './direct-api.mjs';

const lifetime=20*60*1000;
const allowedDomains=['doctolib.de','*.doctolib.de','doctolib.com','*.doctolib.com'];
function state(account){return JSON.parse(account.get('browser_login')||'null');}
function forget(account){account.ctx.storage.sql.exec('DELETE FROM meta WHERE key=?','browser_login');}
async function view(browser,page){
 const cdp=await page.createCDPSession();
 try{
  const result=await cdp.send('Cloudflare.getLiveView',{mode:'tab',expiresInMs:300000});
  const u=new URL(result.devtoolsFrontendUrl);
  if(u.protocol!=='https:'||u.hostname!=='live.browser.run')throw new ApiError('LOGIN_VIEW_INVALID');
  return u.href;
 }finally{await cdp.detach();}
}
export async function browserLogin(account,action,id,driver=puppeteer){
 let current=state(account);
 if(action==='status')return {available:!!account.env.BROWSER,pending:!!current&&current.expires_at>Date.now(),login_id:current?.id,expires_at:current?.expires_at};
 if(!account.env.BROWSER)throw new ApiError('BROWSER_SETUP_REQUIRED',503);
 if(!['start','finish','cancel','expire'].includes(action))throw new ApiError('LOGIN_ACTION_INVALID');
 if(action==='expire'&&(!current||current.expires_at>Date.now()))return {expired:false};
 if(action!=='start'&&action!=='expire'&&(!current||id!==current.id))throw new ApiError('LOGIN_NOT_FOUND',404);
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
  if(current){
   try{browser=await driver.connect(account.env.BROWSER,current.session_id);}catch{forget(account);throw new ApiError('LOGIN_EXPIRED',410);}
  }else{
   browser=await driver.launch(account.env.BROWSER,{keep_alive:600000,guardrails:{allowedDomains,allowedDomainSets:['common-cdns']}});
   close=true;
   current={id:crypto.randomUUID(),session_id:browser.sessionId(),expires_at:Date.now()+lifetime};
   // Persist before navigation so errors/restarts cannot orphan an untracked browser.
   account.set('browser_login',JSON.stringify(current));await account.ctx.storage.sync();
   const pages=await browser.pages();const page=pages[0]||await browser.newPage();
   await page.setViewport({width:390,height:780,deviceScaleFactor:1});
   const response=await page.goto('https://www.doctolib.de/account/appointments',{waitUntil:'domcontentloaded',timeout:30000});
   if(response&&[403,429].includes(response.status()))throw new ApiError('DOCTOLIB_LOGIN_ACCESS_DENIED',response.status());
  }
  const pages=await browser.pages();const page=pages.find(p=>{try{const u=new URL(p.url());return u.protocol==='https:'&&['doctolib.de','doctolib.com'].some(h=>u.hostname===h||u.hostname.endsWith('.'+h));}catch{return false;}});
  if(!page)throw new ApiError('DOCTOLIB_LOGIN_PAGE_REQUIRED');
  if(action==='finish'){
   const cookies=await browser.defaultBrowserContext().cookies();
   const cookie=cookies.filter(c=>['doctolib.de','www.doctolib.de'].includes(c.domain.replace(/^\./,''))&&(!c.expires||c.expires<0||c.expires*1000>Date.now())&&('/account/master_patients.json'.startsWith(c.path||'/'))).map(c=>c.name+'='+c.value).join('; ');
   if(!cookie)throw new ApiError('DOCTOLIB_LOGIN_INCOMPLETE',401);
   const imported=await account.importSession(cookie);
   if(!imported.ok)throw new ApiError(imported.error==='DOCTOLIB_REAUTHENTICATION_REQUIRED'?'DOCTOLIB_LOGIN_INCOMPLETE':imported.error||'SESSION_IMPORT_FAILED');
   close=true;forget(account);return {ok:true,connected:true};
  }
  const login_url=await view(browser,page);close=false;
  return {login_id:current.id,expires_at:current.expires_at,login_url};
 }catch(e){if(close)forget(account);throw e;}
 finally{if(browser){if(close)await browser.close().catch(()=>{});else await browser.disconnect().catch(()=>{});}}
}
