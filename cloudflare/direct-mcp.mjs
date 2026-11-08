import {McpServer,WebStandardStreamableHTTPServerTransport} from '@modelcontextprotocol/server';
import {z} from 'zod';
import {bounded} from './direct-api.mjs';
const id=z.number().int().positive(),ids=z.array(id).min(1).max(20),slug=z.string().regex(/^[A-Za-z0-9_-]{1,200}$/),patient=z.string().regex(/^[A-Za-z0-9_-]{1,200}$/);
const date=z.iso.date(),time=z.string().regex(/^(?:[01]\d|2[0-3]):[0-5]\d$/),iso=z.iso.datetime({offset:true});
const bookFields={start_date:iso,profile_id:id,visit_motive_ids:ids,agenda_ids:ids,practice_ids:ids,patient_id:patient,new_patient:z.boolean(),insurance_sector:z.enum(['public','private']).optional(),confirm:z.boolean().default(false)};
const search={patient_id:patient.optional(),specialty:z.string().min(1).max(200),city:z.string().min(1).max(200),insurance_sector:z.enum(['public','private']).optional(),languages:z.string().max(100).optional(),telehealth:z.boolean().optional(),page:z.number().int().min(0).max(100).default(0)};
const rule={profile_slug:slug,profile_id:id,visit_motive_id:id,agenda_id:id,practice_id:id,patient_id:patient,new_patient:z.boolean(),date_from:date,date_to:date,time_from:time,time_to:time,weekdays:z.array(z.number().int().min(0).max(6)).min(1).max(7),timezone:z.string().min(1).max(100).default('Europe/Berlin'),interval_seconds:z.number().int().min(300).max(86400).default(300),auto_book:z.boolean().default(false),insurance_sector:z.enum(['public','private']).optional()};
export const toolDefinitions=[
 ['list_specialties','Common German specialty slugs; free-text search terms are also accepted.',{},true],
 ['search_doctors','Search doctors using your authenticated German Doctolib session. Missing login is an error; no silent anonymous fallback.',search,true],
 ['get_practitioner','Read the exact chosen doctor/practice profile, motives, calendars and locations.',{profile_slug:slug},true],
 ['get_availabilities','Read slots for an exact visit motive, calendar and practice. Supply the selected patient_id to inherit their insurance automatically. An explicit insurance_sector is only for a user-specified override. No booking.',{patient_id:patient.optional(),visit_motive_id:id,agenda_ids:ids,practice_id:id,insurance_sector:z.enum(['public','private']).optional(),start_date:date.optional(),days:z.number().int().min(1).max(90).default(14)},true],
 ['find_earliest_appointment','Check at most twenty candidates from the first search page. Partial/failing checks are explicit; this is not exhaustive.',{...search,within_days:z.number().int().min(1).max(90).default(14),limit:z.number().int().min(1).max(50).default(10)},true],
 ['booking_url','Return the practice booking link for manual completion.',{profile_slug:slug},true],
 ['my_appointments','Read your personal appointment list. Never returns session credentials.',{},true],
 ['session_status','Check that the configured Doctolib session is valid. Does not accept a password or cookie argument.',{},true],
 ['list_patients','List account holder and linked people with exact stable patient IDs. Select the intended spouse, child or account holder by ID; normalized insurance_sector is included when stored by Doctolib.',{},true],
 ['book_appointment','Preview by default. confirm=true books a real appointment for the exact patient ID. Requires a specific user instruction; never retry an uncertain result.',bookFields,false],
 ['cancel_appointment','Preview by default. confirm=true cancels this exact appointment after the user has specifically requested it. Do not use for checking connectivity.',{appointment_id:z.string().regex(/^[A-Za-z0-9_-]{1,2048}$/),reason:z.string().max(200).optional(),message:z.string().max(1000).optional(),confirm:z.boolean().default(false)},false],
 ['abort_booking_draft','Release a temporary booking draft only; this never cancels a committed appointment.',{},false],
 ['create_appointment_monitor','Create a scheduled instruction for ONE specific doctor/practice, motive, person and allowed times. auto_book=true is a standing user authorization to immediately book a matching slot within those exact criteria. Set it only when the user has explicitly requested this. Checks run by the same Worker every five minutes; stop after success, expiry or any uncertain booking. List existing jobs first.',rule,false],
 ['list_appointment_monitors','List persistent jobs and results. booking/needs_review require account verification before any replacement instruction.',{},true],
 ['stop_appointment_monitor','Stop future checks only. Does not retract an in-flight booking or cancel an existing appointment.',{monitor_id:z.string().uuid()},false]
];
export async function directMcp(request,env){
 const server=new McpServer({name:'changepoint-doctolib',version:'0.3.0'});
 for(const [name,description,properties,readOnly] of toolDefinitions){server.registerTool(name,{description,inputSchema:z.strictObject(properties),annotations:{readOnlyHint:readOnly,destructiveHint:!readOnly,openWorldHint:true}},async args=>{
  try{let value;if(name==='list_specialties')value={specialties:['allgemeinmedizin','zahnarzt','orthopade','dermatologe','augenarzt','hno-arzt','gynakologe','psychotherapeut','radiologe','urologist','kardiologe'],free_text:true};else{if(!env.ACCOUNT_STATE)throw new Error('STORAGE_SETUP_REQUIRED');const result=await env.ACCOUNT_STATE.getByName('personal-de').run(name,args);if(!result.ok)return {isError:true,content:[{type:'text',text:JSON.stringify({error:result.error})}]};value=JSON.parse(result.valueJson);}return {content:[{type:'text',text:JSON.stringify(value)}]};}
  catch(e){const error=/^[A-Z_0-9]+$/.test(e.message||'')?e.message:'OPERATION_FAILED';return {isError:true,content:[{type:'text',text:JSON.stringify({error})}]};}
 });}
 const transport=new WebStandardStreamableHTTPServerTransport({sessionIdGenerator:undefined,enableJsonResponse:true,maxRequestBodySize:524288});await server.connect(transport);
 const text=await bounded(new Response(request.body),524288);const headers=new Headers(request.headers);for(const name of ['authorization','cookie','mcp-session-id','content-length'])headers.delete(name);
 const response=await transport.handleRequest(new Request(request.url,{method:request.method,headers,body:text}));
 const copied=new Headers(response.headers);copied.set('cache-control','no-store');return new Response(response.body,{status:response.status,headers:copied});
}
