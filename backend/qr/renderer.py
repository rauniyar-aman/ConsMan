import base64
import html
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode
import qrcode
import zxingcpp
import resvg_py
from PIL import Image, ImageDraw, ImageColor, ImageFont
from django.conf import settings
from rest_framework.exceptions import ValidationError

MODULES=['square','rounded','dots','extra-rounded','diamond']
EYES=['square','rounded','circle','leaf']
BALLS=['square','rounded','circle','diamond']
DEFAULT={'module':'square','eye_frame':'square','eye_ball':'square','foreground':'#1E3A8A','background':'#FFFFFF','gradient':'','logo':True,'logo_shape':'square','logo_fraction':0.22,'frame':'none','caption':'Scan to register','quiet_zone':4}
LOGO=Path(settings.BASE_DIR).parent/'docs'/'logo_ConsMan.jpg'
if not LOGO.exists():LOGO=Path(__file__).parent/'assets'/'logo_ConsMan.jpg'

def uploaded_logo(raw):
    if raw.lstrip().startswith(b'<'):
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('SVG entities are not allowed.')
        root=ET.fromstring(raw)
        allowed={'svg','g','path','rect','circle','ellipse','polygon','polyline','line','text','defs','linearGradient','radialGradient','stop','clipPath','mask','title','desc'}
        elements=list(root.iter())
        if len(elements)>1000 or root.tag.split('}')[-1]!='svg':raise ValueError('Invalid SVG structure.')
        for item in elements:
            if item.tag.split('}')[-1] not in allowed:raise ValueError('Unsupported SVG element.')
            for key,value in item.attrib.items():
                name=key.split('}')[-1].lower()
                if name.startswith('on') or name in ['href','src'] or re.search(r'http:|https:|file:|data:|@import|javascript:',value,re.I):raise ValueError('External SVG resources are not allowed.')
                if 'url(' in value.lower() and not re.fullmatch(r'url\(#[\w-]+\)',value):raise ValueError('Unsupported SVG reference.')
        return Image.open(io.BytesIO(resvg_py.svg_to_bytes(svg_string=raw.decode('utf-8'),width=256,height=256,skip_system_fonts=True)))
    image=Image.open(io.BytesIO(raw))
    if image.format not in ['PNG','JPEG'] or image.width*image.height>4000000:raise ValueError('Invalid raster logo.')
    return image

def luminance(color):
    rgb=ImageColor.getrgb(color)
    values=[c/255/12.92 if c/255<=0.04045 else ((c/255+0.055)/1.055)**2.4 for c in rgb]
    return .2126*values[0]+.7152*values[1]+.0722*values[2]

def safe_design(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT)-{'logo_data'}:raise ValidationError('Unsupported QR design fields.')
    d={**DEFAULT,**value}
    if d['module'] not in MODULES or d['eye_frame'] not in EYES or d['eye_ball'] not in BALLS:raise ValidationError('Unsupported module or finder style.')
    if d['frame'] not in ['none','border','caption','poster']:raise ValidationError('Unsupported frame style.')
    if not isinstance(d['caption'],str) or len(d['caption'])>100:raise ValidationError('Caption must be at most 100 characters.')
    if d['logo_shape'] not in ['square','rounded','circle']:raise ValidationError('Unsupported logo shape.')
    if type(d['logo'])!=bool:raise ValidationError('Logo must be a boolean.')
    try:
        fraction=float(d['logo_fraction']);quiet=int(d['quiet_zone'])
        if quiet<4 or quiet>10 or fraction<0 or fraction**2>.2:raise ValueError()
        fg=luminance(d['foreground']);bg=luminance(d['background'])
        if bg<=fg or (bg+.05)/(fg+.05)<4.5:raise ValueError()
        if d['gradient'] and ((bg+.05)/(luminance(d['gradient'])+.05)<4.5 or bg<=luminance(d['gradient'])):raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ValidationError('Use dark modules on a light background, contrast ≥4.5:1, a quiet zone ≥4 modules, and logo area ≤20%.')
    d['logo_fraction']=fraction;d['quiet_zone']=quiet
    if d.get('logo_data'):
        try:
            raw=base64.b64decode(d['logo_data'],validate=True)
            if len(raw)>200000:raise ValueError()
            image=uploaded_logo(raw)
            image.verify()
        except Exception:raise ValidationError('Upload a valid PNG/JPEG or safe vector SVG logo under 200 KB. Raster logos must be under 4 megapixels.')
    return d

def payload(qr):
    content=qr.content
    if qr.content_type=='WIFI':
        ssid=content.get('ssid','');password=content.get('password','');security=content.get('security','WPA');hidden=content.get('hidden',False)
        if not isinstance(ssid,str) or not ssid or len(ssid.encode('utf-8'))>32:raise ValidationError('Enter a Wi-Fi network name of 1–32 bytes.')
        if security not in ['WPA','WEP','nopass']:raise ValidationError('Unsupported Wi-Fi security type.')
        if not isinstance(password,str) or len(password)>128 or (security!='nopass' and not password):raise ValidationError('Enter the Wi-Fi password.')
        if type(hidden)!=bool:raise ValidationError('Hidden network must be a boolean.')
        if any(ord(c)<32 for c in ssid+password):raise ValidationError('Wi-Fi credentials cannot contain control characters.')
        def escape(value):return ''.join('\\'+c if c in '\\;,:"' else c for c in value)
        return f'WIFI:T:{security};S:{escape(ssid)};'+(f'P:{escape(password)};' if security!='nopass' else '')+f'H:{str(hidden).lower()};;'
    if qr.content_type=='REGISTRATION':return f'{settings.PUBLIC_FORM_ORIGIN.rstrip("/")}/r/{qr.code}'
    if qr.content_type=='WHATSAPP':
        from crm.services import normalize_phone
        phone=normalize_phone(content.get('phone',''))
        return f'https://wa.me/{phone.lstrip("+")}?{urlencode({"text":str(content.get("message",""))[:1000]})}'
    if qr.content_type=='URL':
        from rest_framework.serializers import URLField
        url=URLField().run_validation(content.get('url'))
        if not url.startswith('https://'):raise ValidationError('Custom links must use HTTPS.')
        return url
    if qr.content_type=='VCARD':
        def esc(value):return str(value).replace('\\','\\\\').replace('\n','\\n').replace(';','\\;').replace(',','\\,').replace('\r','')[:200]
        return '\n'.join(['BEGIN:VCARD','VERSION:3.0',f'FN:{esc(content.get("name","The Blessing Edu"))}',f'TEL:{esc(content.get("phone",""))}',f'EMAIL:{esc(content.get("email",""))}',f'ADR:;;{esc(content.get("address",""))};;;;','END:VCARD'])
    raise ValidationError('Unsupported QR content type.')

def render(qr,payload_text=None):
    d=safe_design(qr.design)
    text=payload(qr) if payload_text is None else payload_text
    if len(text.encode())>2200:raise ValidationError('QR content is too large.')
    code=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H if d['logo'] else qrcode.constants.ERROR_CORRECT_M,border=d['quiet_zone'],box_size=12)
    code.add_data(text);code.make(fit=True)
    matrix=code.get_matrix();n=len(matrix);scale=12;size=n*scale
    image=Image.new('RGB',(size,size),d['background']);draw=ImageDraw.Draw(image)
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">',f'<rect width="100%" height="100%" fill="{html.escape(d["background"],quote=True)}"/>']
    b=d['quiet_zone'];finders=[(b,b),(n-b-7,b),(b,n-b-7)]
    def shape(box,style,color):
        x,y,w,h=box;radius=min(w,h)*.18
        fill=html.escape(color,quote=True)
        if style in ['dots','circle']:
            draw.ellipse((x,y,x+w,y+h),fill=color);svg.append(f'<ellipse cx="{x+w/2}" cy="{y+h/2}" rx="{w/2}" ry="{h/2}" fill="{fill}"/>')
        elif style=='diamond':
            points=[(x+w/2,y),(x+w,y+h/2),(x+w/2,y+h),(x,y+h/2)]
            draw.polygon(points,fill=color);svg.append(f'<polygon points="{" ".join(f"{a},{c}" for a,c in points)}" fill="{fill}"/>')
        elif style=='leaf':
            r=min(w,h)*.35
            draw.rounded_rectangle((x,y,x+w,y+h),radius=r,fill=color)
            draw.rectangle((x,y,x+w-r,y+h-r),fill=color)
            draw.rectangle((x+r,y+r,x+w,y+h),fill=color)
            svg.append(f'<path d="M{x},{y} H{x+w-r} Q{x+w},{y} {x+w},{y+r} V{y+h} H{x+r} Q{x},{y+h} {x},{y+h-r} Z" fill="{fill}"/>')
        else:
            radius=0 if style=='square' else min(w,h)*(.35 if style=='extra-rounded' else .18)
            draw.rounded_rectangle((x,y,x+w,y+h),radius=radius,fill=color);svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}"/>')
    fg=ImageColor.getrgb(d['foreground']);end=ImageColor.getrgb(d['gradient'] or d['foreground'])
    for y,row in enumerate(matrix):
        color='#'+''.join(f'{round(a+(c-a)*y/max(n-1,1)):02x}' for a,c in zip(fg,end))
        for x,on in enumerate(row):
            if not on or any(fx<=x<fx+7 and fy<=y<fy+7 for fx,fy in finders):continue
            inset=1 if d['module'] in ['dots','diamond'] else 0
            shape((x*scale+inset,y*scale+inset,scale-2*inset,scale-2*inset),d['module'],color)
    for x,y in finders:
        style=d['eye_frame']
        shape((x*scale,y*scale,7*scale,7*scale),style,d['foreground'])
        shape(((x+1)*scale,(y+1)*scale,5*scale,5*scale),style,d['background'])
        shape(((x+2)*scale,(y+2)*scale,3*scale,3*scale),d['eye_ball'],d['foreground'])
    if d['logo']:
        logo=uploaded_logo(base64.b64decode(d['logo_data'])) if d.get('logo_data') else Image.open(LOGO)
        logo=logo.convert('RGBA');width=max(1,int((n-2*b)*scale*d['logo_fraction']))
        if d['logo_shape']=='square':
            logo.thumbnail((width,width));badge=Image.new('RGBA',(logo.width+10,logo.height+10),d['background']);badge.paste(logo,(5,5),logo)
        else:
            side=width+10;badge=Image.new('RGBA',(side,side),(0,0,0,0));badge_draw=ImageDraw.Draw(badge)
            if d['logo_shape']=='circle':badge_draw.ellipse((0,0,side-1,side-1),fill=d['background'])
            else:badge_draw.rounded_rectangle((0,0,side-1,side-1),radius=side//5,fill=d['background'])
            inner=max(1,int(width*.70)) if d['logo_shape']=='circle' else max(1,width-4)
            logo.thumbnail((inner,inner));badge.paste(logo,((side-logo.width)//2,(side-logo.height)//2),logo)
        lx=(size-badge.width)//2;ly=(size-badge.height)//2
        image.paste(badge,(lx,ly),badge)
        logo_bytes=io.BytesIO();badge.save(logo_bytes,format='PNG')
        svg.append(f'<image x="{lx}" y="{ly}" width="{badge.width}" height="{badge.height}" href="data:image/png;base64,{base64.b64encode(logo_bytes.getvalue()).decode()}"/>')
    if d['frame']!='none':
        margin=24;bottom=72 if d['frame'] in ['caption','poster'] else margin
        width=size+margin*2;height=size+margin+bottom
        framed=Image.new('RGB',(width,height),d['background']);framed.paste(image,(margin,margin));frame_draw=ImageDraw.Draw(framed)
        frame_draw.rounded_rectangle((2,2,width-3,height-3),radius=16,outline=d['foreground'],width=3)
        svg[0]=f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        svg.insert(2,f'<g transform="translate({margin},{margin})">')
        svg.append('</g>')
        svg.append(f'<rect x="2" y="2" width="{width-4}" height="{height-4}" rx="16" fill="none" stroke="{html.escape(d["foreground"],quote=True)}" stroke-width="3"/>')
        if bottom>margin:
            font=ImageFont.load_default(size=20)
            caption=d['caption'];frame_draw.text((width/2,height-38),caption,fill=d['foreground'],font=font,anchor='mm')
            svg.append(f'<text x="{width/2}" y="{height-30}" text-anchor="middle" font-family="sans-serif" font-size="20" fill="{html.escape(d["foreground"],quote=True)}">{html.escape(caption)}</text>')
        image=framed
    decoded=zxingcpp.read_barcode(image)
    if not decoded or decoded.text!=text:raise ValidationError('This design failed the automatic decode test. Reduce the logo size or choose square/rounded styles.')
    svg.append('</svg>')
    svg_bytes=''.join(svg).encode()
    rendered_svg=resvg_py.svg_to_bytes(svg_string=svg_bytes.decode(),width=image.width,skip_system_fonts=True)
    svg_decoded=zxingcpp.read_barcode(Image.open(io.BytesIO(rendered_svg)))
    if not svg_decoded or svg_decoded.text!=text:raise ValidationError('The SVG output failed decoding. Choose a simpler QR design.')
    png=io.BytesIO();image.save(png,format='PNG')
    return png.getvalue(),svg_bytes,text,d
