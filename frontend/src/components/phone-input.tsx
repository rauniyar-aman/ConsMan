'use client';
import {useEffect,useRef,useState} from 'react';
import {callingCodes} from '@/lib/calling-codes';

const names=new Intl.DisplayNames(['en'],{type:'region'});
const countries=Object.entries(callingCodes).sort(([a],[b])=>a==='NP'?-1:b==='NP'?1:(names.of(a)||a).localeCompare(names.of(b)||b));
function flag(country:string){return Array.from(country.toUpperCase()).map(letter=>String.fromCodePoint(127397+letter.charCodeAt(0))).join('');}
function split(value:string,preferred='NP'){
 const trimmed=value.trim(),digits=trimmed.replace(/\D/g,'');
 if(!trimmed.startsWith('+'))return {country:preferred,number:trimmed};
 const candidates=countries.filter(([,code])=>digits.startsWith(code)).sort((a,b)=>b[1].length-a[1].length);
 const match=candidates.find(([country,code])=>country===preferred&&code.length===candidates[0]?.[1].length)||candidates[0];
 return match?{country:match[0],number:digits.slice(match[1].length)}:{country:preferred,number:trimmed};
}
export function PhoneInput({name,value,defaultValue='',onChange,required=false,label='Phone number',autoComplete='tel'}:{name?:string;value?:string;defaultValue?:string;onChange?:(value:string)=>void;required?:boolean;label?:string;autoComplete?:string}){
 const [parts,setParts]=useState(()=>split(value??defaultValue)),hidden=useRef<HTMLInputElement>(null);
 useEffect(()=>{if(value!==undefined)setParts(previous=>split(value,previous.country));},[value]);
 function combined(next:typeof parts){const digits=next.number.replace(/\D/g,'');return digits?'+'+callingCodes[next.country]+digits:'';}
 function change(next:typeof parts){setParts(next);const phone=combined(next);if(hidden.current)hidden.current.value=phone;onChange?.(phone);}
 return <span className="phone-input"><select title={`${names.of(parts.country)||parts.country} (+${callingCodes[parts.country]})`} aria-label={`${label} country code`} value={parts.country} onChange={event=>change({...parts,country:event.target.value})}>{countries.map(([country,code])=><option key={country} value={country} aria-label={`${names.of(country)||country} (+${code})`}>{flag(country)} +{code}</option>)}</select><input aria-label={label} type="tel" inputMode="tel" autoComplete={autoComplete} value={parts.number} onChange={event=>change(split(event.target.value,parts.country))} required={required} maxLength={30} placeholder={parts.country==='NP'?'98XXXXXXXX':'Phone number'}/><input ref={hidden} type="hidden" name={name} value={combined(parts)}/></span>;
}
