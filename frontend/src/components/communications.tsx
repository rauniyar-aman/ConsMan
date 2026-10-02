'use client';
import {useEffect,useState} from 'react';
import {api,type Detail,type User} from '@/lib/api';
import {ActionForm,choices} from './action-form';

type Row=Record<string,unknown>;
export function Communications({detail,user}:{detail:Detail;user:User}){
 const [history,setHistory]=useState<{consents:Row[];messages:Row[]}>({consents:[],messages:[]});
 const [templates,setTemplates]=useState<Row[]>([]),[error,setError]=useState(''),[form,setForm]=useState<'consent'|'template'|'queue'|null>(null),[busy,setBusy]=useState(false);
 const path=`communications/people/${detail.person.id}/`;
 async function load(){try{const [h,t]=await Promise.all([api<typeof history>(path),api<{results:Row[]}>('communications/templates/')]);setHistory(h);setTemplates(t.results);}catch(e){setError(e instanceof Error?e.message:'Could not load communications.');}}
 useEffect(()=>{void load();},[path]);
 async function cancel(id:unknown){setBusy(true);try{await api(path,'POST',{action:'cancel',message_id:id});await load();}catch(e){setError(e instanceof Error?e.message:'Could not cancel message.');}finally{setBusy(false);}}
 const channel={name:'channel',options:choices(['EMAIL','WHATSAPP','SMS']),required:true};
 const fields=form==='template'?[{name:'name',required:true},channel,{name:'subject'},{name:'body',type:'textarea',required:true}]:form==='consent'?[channel,{name:'allowed',label:'Permission',options:[{id:'true',name:'Consent given'},{id:'false',name:'Opt out'}],required:true},{name:'evidence',label:'Consent source / evidence',type:'textarea',required:true}]:[{name:'template_id',label:'Template',options:templates.map(t=>({id:String(t.id),name:`${t.name} Â· ${t.channel} Â· v${t.version}`})),required:true},{name:'contact_id',label:'Verified recipient',options:detail.contacts.filter(c=>c.verified_at).map(c=>({id:String(c.id),name:`${c.type} Â· ${c.raw_value}`})),required:true},{name:'scheduled_at',type:'datetime-local',label:'Schedule (leave blank for now)'}];
 return <section className="card"><h3>Communications</h3><p className="muted">Messages require verified contacts and explicit channel consent. Delivery workers are being connected; queued messages remain stored.</p>{error&&<p className="error" role="alert">{error}</p>}
 <div className="profile-actions">{user.permissions?.communication_send&&<><button className="secondary" onClick={()=>setForm('consent')}>Record consent / opt out</button><button className="secondary" onClick={()=>setForm('queue')}>Queue message</button></>}{user.permissions?.communication_templates&&<button className="secondary" onClick={()=>setForm('template')}>Create template version</button>}</div>
 {['EMAIL','WHATSAPP','SMS'].map(c=>{const consent=history.consents.find(r=>r.channel===c);return <p key={c}>{c}: {consent?.allowed?'Consent given':consent?'Opted out':'No explicit consent'}</p>;})}
 {history.messages.map(m=><article className="card" key={String(m.id)}><strong>{String(m.channel)} Â· {String(m.status)}</strong><p>{String(m.recipient)}</p>{!!m.subject&&<h4>{String(m.subject)}</h4>}<p style={{whiteSpace:'pre-wrap'}}>{String(m.body)}</p>{user.permissions?.communication_send&&m.status==='QUEUED'&&<button className="secondary" disabled={busy} onClick={()=>void cancel(m.id)}>Cancel queued message</button>}</article>)}
 {form&&<button className="secondary" onClick={()=>setForm(null)}>Close communication form</button>}{form&&<ActionForm title={form==='template'?'Create communication template':form==='consent'?'Record channel permission':'Queue student message'} fields={fields} path={form==='template'?'communications/templates/':path} extra={{action:form==='consent'?'consent':'queue'}} done={()=>{setForm(null);void load();}}/>}</section>;
}
