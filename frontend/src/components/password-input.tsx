'use client';
import {useState,type InputHTMLAttributes} from 'react';
import {Eye,EyeOff} from 'lucide-react';

export function PasswordInput(props:Omit<InputHTMLAttributes<HTMLInputElement>,'type'>) {
 const [visible,setVisible]=useState(false);
 return <span className="password-input">
  <input {...props} type={visible?'text':'password'}/>
  <button type="button" className="password-toggle" disabled={props.disabled} aria-label={visible?'Hide password':'Show password'} aria-pressed={visible} onClick={()=>setVisible(!visible)}>
   {visible?<EyeOff size={18} aria-hidden="true"/>:<Eye size={18} aria-hidden="true"/>}
  </button>
 </span>;
}
