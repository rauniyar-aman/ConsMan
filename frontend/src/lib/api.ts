import type {components} from './generated-api';
export type User = components['schemas']['SessionUser'];
export type CreatePersonInput = components['schemas']['CreatePerson'];
export type Person = { id: string; ref: string; full_name: string; phone: string; email: string; branch_name: string; owner_name: string; source_name: string; stage: string; lead_status: string; temperature: string; preferred_country: string; preferred_course: string; address: string; next_action_due_at: string | null; created_at: string };
export type FollowUp = { id: number; person_id: string; person_name: string; person_ref: string; subject: string; method: string; due_at: string; completed_at: string | null; outcome: string; notes?:string; owner_name?:string; completed_by_name?:string; can_complete?:boolean; created_at?:string; status?:string };
export type Activity = { id: number; type: string; subject: string; notes: string; actor: string; performed_at: string };
export type Detail = { person: Person & Record<string,unknown>; timeline:{id:string;kind:string;subject:string;notes:string;actor:string;at:string;edited:boolean}[]; activities: Activity[]; followups: FollowUp[]; contacts:Row[]; education:Row[]; test_scores:Row[]; tasks:Row[]; team:Row[]; sla_timers:Row[] };
export type Row = Record<string,unknown>;
export type Masters = { branches: {id:number;name:string}[]; sources: {id:number;name:string}[]; owners: {id:number;name:string;branch_id:number}[]; campaigns:{id:number;name:string;source_id:number}[]; source_details:{id:number;name:string;campaign_id:number}[]; tags:{id:number;name:string}[]; lost_reasons:{id:number;name:string}[]; users:{id:number;name:string;role:string;branch_id:number}[] };
export type Dashboard = { unassigned:number;unassigned_breached:number;sla_breaches:number;unverified_intake:number;verification_rate:number;people:number; active_leads:number; hot_leads:number; overdue:number; due_today:number; no_action:number; students:number; pipeline:{status:string;count:number}[]; recent:Person[] };
export class ApiError extends Error {
  constructor(public data: {message?:string;fields?:unknown;code?:string;matches?:{id?:string;confidence?:string;name?:string;ref?:string;owner:string;branch:string;masked?:boolean}[]}, public status:number) { super(data.message || 'Request failed'); }
}
let csrf = '';
export async function api<T>(path: string, method = 'GET', body?: unknown, key?:string): Promise<T> {
  const multipart=body instanceof FormData;
  const response = await fetch(`/api/v1/${path}`, { method, credentials: 'same-origin', headers: { ...(!multipart?{'Content-Type': 'application/json'}:{}), ...(method !== 'GET' ? {'X-CSRFToken': csrf,'Idempotency-Key':key||crypto.randomUUID()} : {}) }, ...(body ? {body: multipart?body:JSON.stringify(body)} : {}) });
  const data = await response.json();
  if (!response.ok) throw new ApiError(data, response.status);
  if (data.csrf_token) csrf = data.csrf_token;
  return data;
}
export function message(error:unknown) { return error instanceof ApiError ? `${error.message}${error.data.fields?' '+JSON.stringify(error.data.fields):''}` : error instanceof Error?error.message:'Please try again.'; }
export const label = (value: string) => value.toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
export const date = (value: string) => new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Kathmandu',day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value));
