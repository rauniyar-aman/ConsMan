 'use client';
import {useEffect,useState} from 'react';
import {Bell,X} from 'lucide-react';
import {api,date,type User} from '@/lib/api';
type Notification={id:number;title:string;message:string;person_id:string|null;read_at:string|null;created_at:string};
export function NotificationPopups({user}:{user:User}){
 const [items,setItems]=useState<Notification[]>([]);
 useEffect(()=>{let active=true,inFlight=false;const storage=`consman-notification-popups:${user.id}`,seen=new Set<number>();try{for(const id of JSON.parse(localStorage.getItem(storage)||'[]'))seen.add(Number(id));}catch{}
 async function refresh(){if(inFlight||document.visibilityState==='hidden')return;inFlight=true;try{const rows=await api<Notification[]>('notifications/');if(!active)return;try{for(const id of JSON.parse(localStorage.getItem(storage)||'[]'))seen.add(Number(id));}catch{}const fresh=rows.filter(n=>!n.read_at&&!seen.has(n.id)).slice(0,3);for(const n of rows)seen.add(n.id);try{localStorage.setItem(storage,JSON.stringify(Array.from(seen).slice(-500)));}catch{}setItems(current=>[...fresh,...current.filter(n=>rows.some(row=>row.id===n.id&&!row.read_at))].slice(0,3));}catch{}finally{inFlight=false;}}
 void refresh();const timer=setInterval(()=>void refresh(),15000);const visible=()=>void refresh();document.addEventListener('visibilitychange',visible);return()=>{active=false;clearInterval(timer);document.removeEventListener('visibilitychange',visible);};},[user.id]);
 return <aside className="notification-popups" aria-label="New notifications" aria-live="polite">{items.map(n=><article className="notification-popup" key={n.id}><div className="notification-popup-heading"><Bell size={18}/><strong>{n.title}</strong><button aria-label="Dismiss notification" onClick={()=>setItems(current=>current.filter(i=>i.id!==n.id))}><X size={18}/></button></div>{n.message&&<p>{n.message}</p>}<small>{date(n.created_at)}</small><div className="toolbar">{n.person_id&&<a className="secondary" href={`/people/${n.person_id}`} target="_blank" rel="noreferrer">Open profile</a>}<button className="text-button" onClick={async()=>{try{await api('notifications/','POST',{ids:[n.id]});setItems(current=>current.filter(i=>i.id!==n.id));}catch{}}}>Mark read</button></div></article>)}</aside>;
}
