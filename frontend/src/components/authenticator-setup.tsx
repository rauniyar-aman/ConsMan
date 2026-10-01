'use client';
import {useEffect,useRef,useState} from 'react';
import QRCode from 'qrcode';

export function AuthenticatorSetup({uri,secret}:{uri:string;secret:string}) {
 const canvas=useRef<HTMLCanvasElement>(null);
 const [manual,setManual]=useState(false),[failed,setFailed]=useState(false);
 useEffect(()=>{
  setManual(false);setFailed(false);
  if(canvas.current) void QRCode.toCanvas(canvas.current,uri,{width:220,margin:4,errorCorrectionLevel:'M'}).catch(()=>setFailed(true));
 },[uri]);
 return <div className="authenticator-setup">
  <p>Scan this QR code with your authenticator app. Then enter the six-digit code from that app below.</p>
  {!failed&&<canvas ref={canvas} role="img" aria-label="Authenticator setup QR code" style={{display:'block',maxWidth:'100%',height:'auto',margin:'12px auto'}}/>}
  {failed&&<p role="alert">The QR code could not be generated. Use the manual setup option.</p>}
  <button type="button" className="text-button" aria-expanded={manual} onClick={()=>setManual(!manual)}>{manual?'Hide setup key':'Cannot scan? Enter a setup key manually'}</button>
  {manual&&<><p>Choose a time-based account in your authenticator app and enter this setup key. This key is not the six-digit verification code.</p><code className="mfa-secret">{secret}</code></>}
 </div>;
}
