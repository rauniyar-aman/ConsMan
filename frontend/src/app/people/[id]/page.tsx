 'use client';
import {use,useEffect,useState,useCallback} from 'react';
import {api,message,type User,type Masters,type Detail,type Row} from '@/lib/api';
import {NotificationPopups} from '@/components/notification-popups';
import {PersonPanel} from '@/components/person-profile';
import {ActionForm,reason} from '@/components/action-form';
export default function ProfilePage({params}:{params:Promise<{id:string}>}){
 const {id}=use(params),[user,setUser]=useState<User|null>(null),[masters,setMasters]=useState<Masters|null>(null),[detail,setDetail]=useState<Detail|null>(null),[masked,setMasked]=useState<Row|null>(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[toast,setToast]=useState('');
 const refresh=useCallback(async()=>{try{const session=await api<{user:User|null}>('auth/session/');setUser(session.user);if(!session.user)return;const [m,d]=await Promise.all([api<Masters>('masters/'),api<Detail & {masked?:boolean;record:Row}>(`people/${id}/`)]);setMasters(m);if(d.masked)setMasked(d.record);else setDetail(d);}catch(e){setError(message(e));}finally{setLoading(false);}},[id]);
 useEffect(()=>{void refresh();},[refresh]);useEffect(()=>{document.title=`${detail?detail.person.full_name+' · Profile':'Person profile'} | ConsMan`;},[detail]);
 async function saved(text:string){setToast(text);await refresh();}
 if(loading)return <main className="loading">Opening profile…</main>;
 if(!user)return <main><h1>Sign in required</h1><p>Sign in to the workspace, then reload this profile tab.</p><a className="primary" href="/" target="_blank" rel="noreferrer">Open sign in</a></main>;
 return <><NotificationPopups user={user}/>{error&&<main><p className="error" role="alert">{error}</p><button className="secondary" onClick={()=>void refresh()}>Retry</button><a href="/">Back to workspace</a></main>}{masked&&<main className="card record-card"><h1>Existing record</h1><p>Owner: {String(masked.owner_name)} · {String(masked.branch_name)}</p>{user.permissions?.request_access&&<ActionForm title="Request access" path="access-requests/" fields={[reason]} extra={{person_id:id}} done={()=>setToast('Access requested.')}/>}<a href="/">Back to workspace</a></main>}{detail&&masters&&<PersonPanel detail={detail} user={user} masters={masters} close={()=>{window.location.href='/';}} saved={saved} busy={busy} setBusy={setBusy}/>} {!detail&&!masked&&!error&&<main>Profile unavailable.</main>}{toast&&<div className="toast" role="status" onClick={()=>setToast('')}>{toast}</div>}</>;
}
