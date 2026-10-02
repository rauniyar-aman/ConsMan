import json
from pathlib import Path
from rest_framework.exceptions import ValidationError
FIELDS=json.loads(Path(__file__).with_name('form_fields.json').read_text(encoding='utf-8'))
def form_config(content):
    custom=content.get('form',{})
    if not isinstance(custom,dict) or set(custom)-{'title','description','fields'}:raise ValidationError('Invalid form configuration.')
    overrides=custom.get('fields',{})
    legacy={'test_'+t for t in ['IELTS','PTE','TOEFL','DUOLINGO','SAT','GRE','GMAT','OTHER']}|{'test_status'}
    if not isinstance(overrides,dict) or set(overrides)-{f['name'] for f in FIELDS}-legacy:raise ValidationError('Unknown form fields.')
    if any(isinstance(overrides.get(n),dict) and overrides[n].get('required') is True for n in legacy) and 'english_test' not in overrides:overrides={**overrides,'english_test':{'required':True}}
    result=[]
    for field in FIELDS:
        setting=overrides.get(field['name'],{})
        if not isinstance(setting,dict) or set(setting)-{'visible','required'} or any(type(v) is not bool for v in setting.values()):raise ValidationError('Field settings must be boolean.')
        required=field['locked'] or setting.get('required',field['required'])
        result.append({**field,'required':required,'visible':required or setting.get('visible',True)})
    title=custom.get('title','Visitor registration form')
    description=custom.get('description','Please complete your main details. Fields marked * are required; all other fields are optional.')
    if not isinstance(title,str) or not title.strip() or len(title)>160 or not isinstance(description,str) or len(description)>1000:raise ValidationError('Invalid form title or description.')
    return {'title':title,'description':description,'fields':result}
def validate_answers(qr,data):
    errors={}
    for f in form_config(qr.content)['fields']:
        name=f['name']
        value=data.get(name)
        if name in ['institute','degree_stream','grade_or_percent','passed_year']:value=next(iter(data.get('education',[])),{}).get(name)
        if name.startswith('test_') and name!='test_status':value=next((x.get('score') for x in data.get('test_scores',[]) if x['test']==name[5:]),None)
        if name=='test_status':value=next((x.get('status') for x in data.get('test_scores',[])),None)
        if name=='english_score':
            if data.get('english_test') in ['NOT_TAKEN','WITHOUT_TEST']:continue
            value=next((x.get('score') for x in data.get('test_scores',[])),None)
        if name=='english_other' and data.get('english_test')!='OTHER':continue
        if f['required'] and not value:errors[name]='This field is required.'
    if errors:raise ValidationError(errors)
