import {test, before, after} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import puppeteer from '@cloudflare/puppeteer';
import {loginInteraction} from '../cloudflare/login-input.mjs';
import {connectHTML,connectJS} from '../cloudflare/connect-page.mjs';

let browser;
const connect=()=>puppeteer.connect({browserURL:'http://127.0.0.1:9222',defaultViewport:{width:390,height:780}});
before(async()=>{browser=await connect();});
after(async()=>{if(browser)await browser.disconnect();});
const fixtureURL='https://www.doctolib.de/authn/patient/local-test-fixture';
const fixtureHTML='<!doctype html><html><body><form id="form"><label for="username">E-Mail</label><input id="username" type="text"><label for="password">Passwort</label><input id="password" type="password"><button>Weiter</button></form><script>window.submits=0;document.querySelector("form").addEventListener("submit",event=>{event.preventDefault();window.submits++;});</script></body></html>';

test('real browser applies email and password after a CDP reconnect and background focus',async()=>{
 const page=await browser.newPage();
 await page.setRequestInterception(true);
 page.on('request',request=>request.url()===fixtureURL?request.respond({status:200,contentType:'text/html',body:fixtureHTML}):request.abort());
 await page.goto(fixtureURL,{waitUntil:'domcontentloaded'});
 await page.click('#username');
 await browser.disconnect();
 browser=await connect();
 const remote=(await browser.pages()).find(p=>p.url()===fixtureURL);
 assert.ok(remote);
 const other=await browser.newPage();await other.bringToFront();
 await remote.setViewport({width:390,height:780});
 const value='fixture-user@example.invalid';
 await loginInteraction(remote,{kind:'text',text:value});
 assert.equal(await remote.$eval('#username',e=>e.value),value);
 assert.equal(await remote.evaluate(()=>window.submits),0);
 await remote.click('#password');
 const password='FAKE_PASSWORD_FOR_BROWSER_TEST_ONLY';
 const result=await loginInteraction(remote,{kind:'text',text:password});
 assert.equal(await remote.$eval('#password',e=>e.value),password);
 assert.equal(result.field.type,'password');
 assert.ok(!JSON.stringify(result).includes(password));
 assert.equal(await remote.evaluate(()=>window.submits),0);
 await other.close();
});

test('mobile client transfers text visibly and waits for a separate Weiter action',async()=>{
 const fixture=(await browser.pages()).find(p=>p.url()===fixtureURL);
 const image=await fixture.screenshot({type:'jpeg',encoding:'base64'});
 const calls=[];
 const id='00000000-0000-0000-0000-000000000001';
 const frame={image,field:{type:'password',label:'Passwort'},width:390,height:780};
 const server=createServer(async(req,res)=>{
  try{
   if(req.url==='/connect.js'){res.writeHead(200,{'content-type':'text/javascript'});res.end(connectJS);return;}
   if(req.url==='/connect'){res.writeHead(200,{'content-type':'text/html'});res.end(connectHTML);return;}
   let body='';for await(const chunk of req)body+=chunk;
   const data=body?JSON.parse(body):{};
   let result;
   if(req.url==='/connect/status')result={available:true,pending:true,login_id:id};
   else if(req.url==='/connect/frame')result=frame;
   else if(req.url==='/connect/interact'){assert.equal(data.login_id,id);calls.push(data.interaction);result=frame;}
   else{res.writeHead(404);res.end();return;}
   res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(result));
  }catch{res.writeHead(500);res.end('{}');}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const ui=await browser.newPage();
 try{
  await ui.setViewport({width:390,height:844,isMobile:true,hasTouch:true});
  await ui.goto('http://127.0.0.1:'+server.address().port+'/connect');
  await ui.waitForSelector('#openInline',{visible:true});await ui.click('#openInline');
  await ui.waitForSelector('#remoteText:not([disabled])');
  assert.equal(await ui.$eval('#remoteText',e=>e.type),'password');
  await ui.click('#remoteText');await ui.keyboard.type('FAKE_NATIVE_INPUT_ONLY');
  await ui.click('#sendText');
  await ui.waitForFunction(()=>document.querySelector('#message').textContent.includes('Eingabe wurde ins Doctolib-Feld'));
  assert.deepEqual(calls,[{kind:'text',text:'FAKE_NATIVE_INPUT_ONLY'}]);
  assert.equal(await ui.$eval('#remoteText',e=>e.value),'');
  const next=ui.waitForResponse(r=>r.url().endsWith('/connect/interact')&&r.request().postData()?.includes('"kind":"key"'));
  await ui.click('#remoteEnter');await next;
  assert.deepEqual(calls.map(c=>c.kind),['text','key']);
  assert.equal(calls[1].key,'Enter');
 }finally{
  await ui.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
 }
});
