import csv,hashlib,io
from decimal import Decimal
from pathlib import Path
from django.db import transaction
from django.db.models import Q,Max,Sum,F,OuterRef,Subquery,Value,DecimalField
from django.db.models.functions import Coalesce
from django.http import HttpResponse,StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied,ValidationError
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from crm.models import Person,Branch
from crm.permissions import profile,scope,MATRIX
from crm.operations import required_reason
from crm.services import audit
from crm.idempotency import idempotent
from admissions.models import Application,University
from progression.models import Payment,JourneyCounter
from progression.api import ref,reviewed_document
from .models import Invoice,Expense,CommissionRule,InstitutionCommission,PaymentAllocation,Receipt,CommissionReceipt,FinanceFile,FinanceEvent
from .services import commission_amount,allocate_payment

KINDS={'invoices':Invoice,'payments':Payment,'expenses':Expense,'rules':CommissionRule,'commissions':InstitutionCommission,'receipts':Receipt,'commission_receipts':CommissionReceipt}
BRANCH_FIELDS={'invoices':'person__branch_id','payments':'person__branch_id','expenses':'branch_id','commissions':'application__person__branch_id','receipts':'payment__person__branch_id','commission_receipts':'commission__application__person__branch_id'}


def access(request,write=False):
    scope(request.user,'finance_edit' if write else 'finance_view')
    return profile(request.user)


def scoped(request,kind):
    staff=access(request);qs=KINDS[kind].objects.all()
    if staff.role in ['MANAGER','FINANCE'] and kind in BRANCH_FIELDS:qs=qs.filter(**{BRANCH_FIELDS[kind]:staff.branch_id})
    return qs


def people(request):
    staff=access(request);qs=Person.objects.filter(archived_at__isnull=True)
    return qs.filter(branch_id=staff.branch_id) if staff.role in ['MANAGER','FINANCE'] else qs


def applications(request):return Application.objects.filter(person__in=people(request)).select_related('person','course_offering__campus__university')
def uid(data,key='id'):return serializers.UUIDField().run_validation(data.get(key))
def integer(data,key='id'):return serializers.IntegerField(min_value=1).run_validation(data.get(key))
def text(data,key,length=160,blank=False):return serializers.CharField(max_length=length,allow_blank=blank).run_validation(data.get(key,''))
def money(data,key='amount',zero=False):return serializers.DecimalField(max_digits=12,decimal_places=2,min_value=Decimal(0) if zero else Decimal('0.01')).run_validation(data.get(key))
def currency(data):return serializers.RegexField(r'^[A-Za-z]{3}$').run_validation(data.get('currency')).upper()
def day(data,key,optional=False):return serializers.DateField(allow_null=optional,required=not optional).run_validation(data.get(key))
def record(request,kind,value,lock=False):
    if kind not in KINDS:raise ValidationError('Unknown finance record type.')
    qs=scoped(request,kind)
    if lock:qs=qs.select_for_update(of=('self',))
    return get_object_or_404(qs,pk=serializers.IntegerField(min_value=1).run_validation(value) if kind=='rules' else serializers.UUIDField().run_validation(value))


def history(request,kind,obj,action,new=None,old=None,reason=''):
    branch=obj.branch if kind=='expenses' else obj.person.branch if kind in ['invoices','payments'] else obj.application.person.branch if kind=='commissions' else obj.payment.person.branch if kind=='receipts' else obj.commission.application.person.branch if kind=='commission_receipts' else profile(request.user).branch
    FinanceEvent.objects.create(object_kind=kind,object_id=str(obj.pk),branch=branch,actor=request.user,action=action,new=new or {},old=old or {},reason=reason)
    obj.branch=branch
    audit(request,'FINANCE_'+action,obj,new={'kind':kind,**(new or {})},old=old)


def issue_receipt(request,obj):
    receipt,created=Receipt.objects.get_or_create(payment=obj,defaults={'reference':ref('REC'),'snapshot':{'person_name':obj.person.full_name,'person_reference':obj.person.ref,'payment_reference':obj.ref,'amount':str(obj.amount),'currency':obj.currency,'paid_on':str(obj.paid_on),'method':obj.method,'transaction_reference':obj.reference,'purpose':obj.purpose,'branch_name':obj.person.branch.name,'proof_files':files(obj.financial_files),'proof_document_id':str(obj.proof_id) if obj.proof_id else None,'proof_document_version':obj.proof.version if obj.proof else None},'created_by':request.user})
    if created:history(request,'receipts',receipt,'RECEIPT_ISSUED',new={'reference':receipt.reference})

def invoice_paid(obj):
    if 'allocations' in getattr(obj,'_prefetched_objects_cache',{}):return sum((a.amount for a in obj.allocations.all() if a.payment.status=='VERIFIED'),Decimal(0))
    return obj.allocations.filter(payment__status='VERIFIED').aggregate(t=Sum('amount'))['t'] or Decimal(0)
def commission_received(obj):
    if 'receipts' in getattr(obj,'_prefetched_objects_cache',{}):return sum((r.amount for r in obj.receipts.all()),Decimal(0))
    return obj.receipts.aggregate(t=Sum('amount'))['t'] or Decimal(0)
def files(qs):return [{**row,'created_at':row['created_at'].isoformat()} for row in qs.values('id','filename','mime','checksum','created_at')]


def serialize(kind,obj):
    fields={f.attname:str(getattr(obj,f.attname)) if isinstance(getattr(obj,f.attname),Decimal) else getattr(obj,f.attname) for f in obj._meta.concrete_fields if f.name not in ['content']}
    fields['id']=str(obj.pk)
    if kind in ['invoices','payments']:
        fields.update(person_name=obj.person.full_name,person_reference=obj.person.ref,branch_id=obj.person.branch_id)
    if kind=='invoices':
        paid=invoice_paid(obj);fields.update(paid=str(paid),balance=str(obj.amount-paid),settlement='PAID' if paid>=obj.amount else 'PARTIAL' if paid else 'UNPAID',allocations=list(obj.allocations.values('id','payment_id','amount')))
    if kind=='payments':
        fields.update(files=files(obj.financial_files),receipt_id=str(obj.receipt.pk) if Receipt.objects.filter(payment=obj).exists() else None,allocated=str(obj.allocations.aggregate(t=Sum('amount'))['t'] or Decimal(0)))
    if kind=='expenses':fields['files']=files(obj.financial_files)
    if kind=='commissions':
        received=commission_received(obj);fields.update(received=str(received),balance=str(obj.expected_amount-received),institution=obj.rule.institution.name,person_name=obj.application.person.full_name,application_reference=obj.application.ref,receipts=[serialize('commission_receipts',r) for r in obj.receipts.all()])
    if kind=='commission_receipts':fields.update(files=files(obj.financial_files),currency=obj.commission.currency,institution=obj.commission.rule.institution.name)
    if kind=='rules':fields['institution_name']=obj.institution.name
    if kind=='receipts':fields.update(obj.snapshot);fields['current_payment_status']=obj.payment.status
    return fields


def selected(request,kind):
    qs=scoped(request,kind).order_by('-pk')
    related={'invoices':['person__branch'],'payments':['person__branch','proof'],'expenses':['branch'],'rules':['institution'],'commissions':['application__person','rule__institution'],'receipts':['payment'],'commission_receipts':['commission__rule__institution']}
    qs=qs.select_related(*related[kind])
    if kind=='invoices':qs=qs.prefetch_related('allocations__payment')
    if kind=='commissions':qs=qs.prefetch_related('receipts__financial_files')
    search=request.query_params.get('search','').strip()[:160];state=request.query_params.get('status','')
    if state and kind in ['invoices','payments','expenses','commissions']:qs=qs.filter(status=state)
    if search:
        lookup=Q(name__icontains=search)|Q(institution__name__icontains=search) if kind=='rules' else Q(reference__icontains=search) if kind!='payments' else Q(ref__icontains=search)
        if kind in ['invoices','payments']:lookup|=Q(person__full_name__icontains=search)|Q(person__ref__icontains=search)
        if kind=='expenses':lookup|=Q(payee__icontains=search)|Q(category__icontains=search)
        if kind=='commissions':lookup|=Q(application__person__full_name__icontains=search)|Q(rule__institution__name__icontains=search)
        qs=qs.filter(lookup)
    return qs


def summary(request):
    totals={}
    def add(qs,column,key):
        for row in qs.values('currency').annotate(total=Sum(column)):
            totals.setdefault(row['currency'],{'currency':row['currency']})[key]=str(row['total'] or 0)
    add(scoped(request,'payments').filter(status='VERIFIED'),'amount','verified_payments')
    add(scoped(request,'expenses').filter(status='VERIFIED'),'amount','verified_expenses')
    add(scoped(request,'commissions').exclude(status='CANCELLED'),'expected_amount','expected_commissions')
    receipts=scoped(request,'commission_receipts')
    for row in receipts.values('commission__currency').annotate(total=Sum('amount')):
        code=row['commission__currency'];totals.setdefault(code,{'currency':code})['received_commissions']=str(row['total'])
    paid=PaymentAllocation.objects.filter(invoice_id=OuterRef('pk'),payment__status='VERIFIED').values('invoice_id').annotate(total=Sum('amount')).values('total')
    balances=scoped(request,'invoices').filter(status='ISSUED').annotate(paid=Coalesce(Subquery(paid),Value(Decimal(0)),output_field=DecimalField(max_digits=12,decimal_places=2))).values('currency').annotate(total=Sum(F('amount')-F('paid')))
    for row in balances:
        code=row['currency'];totals.setdefault(code,{'currency':code})['invoice_outstanding']=str(row['total'])
    return list(totals.values())


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
@idempotent()
def workspace(request):
    access(request,request.method=='POST')
    if request.method=='POST':
        data=request.data;action=data.get('action')
        if action=='invoice_create':
            person=get_object_or_404(people(request),pk=uid(data,'person_id'));app=get_object_or_404(applications(request),pk=uid(data,'application_id')) if data.get('application_id') else None
            if app and app.person_id!=person.pk:raise ValidationError('Application must belong to the invoice person.')
            obj=Invoice.objects.create(reference=ref('INV'),person=person,application=app,description=text(data,'description',500),amount=money(data),currency=currency(data),due_on=day(data,'due_on',True),created_by=request.user)
            history(request,'invoices',obj,'INVOICE_CREATED',new={'reference':obj.reference,'amount':str(obj.amount),'currency':obj.currency});kind='invoices'
        elif action in ['invoice_edit','invoice_status']:
            obj=record(request,'invoices',data.get('id'),True);kind='invoices';reason=required_reason(data)
            if action=='invoice_edit':
                if obj.status!='DRAFT':raise ValidationError('Only draft invoices can be edited.')
                obj.description=text(data,'description',500);obj.amount=money(data);obj.currency=currency(data);obj.due_on=day(data,'due_on',True);obj.save()
            else:
                state=serializers.ChoiceField(choices=['ISSUED','CANCELLED']).run_validation(data.get('status'))
                if obj.status=='CANCELLED' or state=='ISSUED' and obj.status!='DRAFT':raise ValidationError('Invalid invoice transition.')
                if state=='CANCELLED' and invoice_paid(obj)>0:raise ValidationError('Refund allocated payments before cancelling an invoice.')
                if state=='ISSUED':obj.issued_snapshot={'person_name':obj.person.full_name,'person_reference':obj.person.ref,'description':obj.description,'amount':str(obj.amount),'currency':obj.currency,'due_on':str(obj.due_on or ''),'issued_on':str(timezone.localdate())}
                obj.status=state;obj.save(update_fields=['status','issued_snapshot'])
            history(request,kind,obj,action.upper(),new={'status':obj.status,'amount':str(obj.amount),'currency':obj.currency,'description':obj.description,'issued_snapshot':obj.issued_snapshot},reason=reason)
        elif action=='payment_create':
            person=get_object_or_404(people(request),pk=uid(data,'person_id'));app=get_object_or_404(applications(request),pk=uid(data,'application_id')) if data.get('application_id') else None
            if app and app.person_id!=person.pk:raise ValidationError('Application must belong to the payment person.')
            purpose=serializers.ChoiceField(choices=['DEPOSIT','FEE','OTHER']).run_validation(data.get('purpose','FEE'))
            if purpose=='DEPOSIT' and not app:raise ValidationError('Institution deposits require an application.')
            obj=Payment.objects.create(ref=ref('PAY'),person=person,application=app,amount=money(data),currency=currency(data),paid_on=day(data,'paid_on'),method=serializers.ChoiceField(choices=['CASH','BANK_TRANSFER','CARD','ONLINE','OTHER']).run_validation(data.get('method')),reference=text(data,'reference',120,True),purpose=purpose,created_by=request.user);kind='payments'
            history(request,kind,obj,'PAYMENT_CREATED',new={'ref':obj.ref,'amount':str(obj.amount),'currency':obj.currency,'purpose':purpose})
        elif action=='payment_status':
            obj=record(request,'payments',data.get('id'),True);kind='payments';state=data.get('status');reason=required_reason(data);old=obj.status
            if state not in {'PENDING':['RECEIVED','CANCELLED'],'RECEIVED':['VERIFIED','CANCELLED'],'VERIFIED':['REFUNDED']}.get(old,[]):raise ValidationError('Invalid payment transition.')
            if state=='VERIFIED':
                if not obj.financial_files.exists() and (not obj.proof or not reviewed_document(obj.proof)):raise ValidationError('Upload payment proof before verification.')
                obj.verified_by=request.user
            obj.status=state;obj.save(update_fields=['status','verified_by'])
            history(request,kind,obj,'PAYMENT_STATUS',old={'status':old},new={'status':state},reason=reason)
            if state=='VERIFIED':
                issue_receipt(request,obj)
        elif action=='allocate':
            invoice=record(request,'invoices',data.get('invoice_id'),True);payment=record(request,'payments',data.get('payment_id'),True)
            allocation=allocate_payment(invoice.pk,payment.pk,money(data),request.user);obj=invoice;kind='invoices'
            history(request,kind,obj,'PAYMENT_ALLOCATED',new={'allocation_id':allocation.pk,'payment_id':str(payment.pk),'amount':str(allocation.amount)},reason=required_reason(data))
        elif action=='expense_create':
            staff=profile(request.user);branch=get_object_or_404(Branch,pk=integer(data,'branch_id'))
            if staff.role in ['MANAGER','FINANCE'] and branch.pk!=staff.branch_id:raise PermissionDenied('Use your finance branch.')
            obj=Expense.objects.create(reference=ref('EXP'),branch=branch,category=text(data,'category',80),payee=text(data,'payee'),description=text(data,'description',2000,True),amount=money(data),currency=currency(data),incurred_on=day(data,'incurred_on'),created_by=request.user);kind='expenses'
            history(request,kind,obj,'EXPENSE_CREATED',new={'reference':obj.reference,'amount':str(obj.amount),'currency':obj.currency})
        elif action=='expense_status':
            obj=record(request,'expenses',data.get('id'),True);kind='expenses';state=data.get('status');reason=required_reason(data);old=obj.status
            if state not in {'PENDING':['VERIFIED','CANCELLED'],'VERIFIED':['VOIDED']}.get(old,[]):raise ValidationError('Invalid expense transition.')
            if state=='VERIFIED':
                if not obj.financial_files.exists():raise ValidationError('Upload expense proof before verification.')
                obj.verified_by=request.user
            obj.status=state;obj.save();history(request,kind,obj,'EXPENSE_STATUS',old={'status':old},new={'status':state},reason=reason)
        elif action=='rule_create':
            scope(request.user,'finance_rules')
            institution=get_object_or_404(University,pk=integer(data,'institution_id'));name=text(data,'name');method=serializers.ChoiceField(choices=['FIXED','PERCENTAGE']).run_validation(data.get('method'));value=money(data,'value',True)
            if method=='PERCENTAGE' and value>100:raise ValidationError('Percentage cannot exceed 100.')
            counter,_=JourneyCounter.objects.get_or_create(kind='COM_RULE',year=0);JourneyCounter.objects.select_for_update().get(pk=counter.pk)
            version=(CommissionRule.objects.filter(institution=institution,name=name).aggregate(v=Max('version'))['v'] or 0)+1
            obj=CommissionRule.objects.create(institution=institution,name=name,version=version,method=method,value=value,currency=currency(data),created_by=request.user);kind='rules'
            history(request,kind,obj,'COMMISSION_RULE_CREATED',new={'version':version,'method':method,'value':str(value),'currency':obj.currency})
        elif action=='commission_create':
            app=get_object_or_404(applications(request).select_for_update(of=('self',)),pk=uid(data,'application_id'));rule=record(request,'rules',data.get('rule_id'))
            if rule.institution_id!=app.course_offering.campus.university_id:raise ValidationError('Commission rule must match the application institution.')
            if InstitutionCommission.objects.filter(application=app,rule=rule).exclude(status='CANCELLED').exists():raise ValidationError('This application/rule commission is already recorded.')
            base=money(data,'base_amount',True);code=currency(data);expected=commission_amount(rule,base,code)
            if expected>Decimal('9999999999.99'):raise ValidationError('Commission exceeds the supported amount range.')
            obj=InstitutionCommission.objects.create(reference=ref('COM'),application=app,rule=rule,rule_snapshot={'institution_id':rule.institution_id,'institution_name':rule.institution.name,'name':rule.name,'version':rule.version,'method':rule.method,'value':str(rule.value),'currency':rule.currency},base_amount=base,expected_amount=expected,currency=code,status='RECEIVED' if expected==0 else 'PENDING',due_on=day(data,'due_on',True),created_by=request.user);kind='commissions'
            history(request,kind,obj,'COMMISSION_CREATED',new={'expected_amount':str(expected),'currency':code,'rule_snapshot':obj.rule_snapshot},reason=required_reason(data))
        elif action=='commission_receive':
            obj=record(request,'commissions',data.get('id'),True);kind='commissions';total=money(data)
            if obj.status=='CANCELLED' or commission_received(obj)+total>obj.expected_amount:raise ValidationError('Receipt exceeds the outstanding commission or commission is cancelled.')
            receipt=CommissionReceipt.objects.create(reference=ref('CR'),commission=obj,amount=total,received_on=day(data,'received_on'),transaction_reference=text(data,'transaction_reference',120),created_by=request.user)
            obj.status='RECEIVED' if commission_received(obj)==obj.expected_amount else 'PARTIAL';obj.save(update_fields=['status'])
            history(request,'commission_receipts',receipt,'COMMISSION_RECEIPT',new={'amount':str(total),'reference':receipt.reference},reason=required_reason(data))
            history(request,kind,obj,'COMMISSION_RECEIVED',new={'receipt_id':str(receipt.pk),'status':obj.status,'amount':str(total)})
        elif action=='commission_cancel':
            obj=record(request,'commissions',data.get('id'),True);kind='commissions'
            if obj.status=='CANCELLED' or obj.receipts.exists():raise ValidationError('Only unreceived commissions can be cancelled.')
            obj.status='CANCELLED';obj.save(update_fields=['status']);history(request,kind,obj,'COMMISSION_CANCELLED',reason=required_reason(data))
        else:raise ValidationError('Unknown finance action.')
        return Response({'kind':kind,'record':serialize(kind,obj)},status=201 if action.endswith('_create') else 200)
    kind=request.query_params.get('kind','invoices')
    if kind not in KINDS:raise ValidationError('Unknown finance section.')
    offset=serializers.IntegerField(min_value=0).run_validation(request.query_params.get('offset',0));qs=selected(request,kind)
    staff=profile(request.user);branches=Branch.objects.filter(pk=staff.branch_id) if staff.role in ['MANAGER','FINANCE'] else Branch.objects.all()
    return Response({'kind':kind,'count':qs.count(),'results':[serialize(kind,obj) for obj in qs[offset:offset+50]],'summary':summary(request),'can_edit':bool(MATRIX[staff.role].get('finance_edit')),'can_manage_rules':bool(MATRIX[staff.role].get('finance_rules')),'people':list(people(request).order_by('full_name').values('id','full_name','ref')[:1000]),'applications':list(applications(request).order_by('-created_at').values('id','ref','person_id','person__full_name','course_offering__campus__university_id')[:1000]),'branches':list(branches.values('id','name')),'institutions':list(University.objects.values('id','name')),'rules':[serialize('rules',r) for r in CommissionRule.objects.order_by('-version')[:1000]]})


def valid_file(upload):
    import pymupdf
    from PIL import Image
    if not upload or upload.size>5*1024*1024:raise ValidationError('Upload a PDF, PNG or JPEG up to 5 MB.')
    raw=upload.read()
    try:
        if raw.startswith(b'%PDF-'):
            with pymupdf.open(stream=raw,filetype='pdf') as pdf:
                if pdf.is_encrypted or not len(pdf) or pdf.embfile_count():raise ValueError()
            if any(marker in raw for marker in [b'/JavaScript',b'/JS',b'/Launch']):raise ValueError()
            mime='application/pdf'
        else:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ['PNG','JPEG'] or image.width*image.height>25000000:raise ValueError()
                mime='image/png' if image.format=='PNG' else 'image/jpeg';image.verify()
    except Exception:raise ValidationError('Upload a readable PDF/PNG/JPEG without active scripts, encryption or attachments.')
    return raw,mime


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
def upload(request,kind,pk):
    access(request,True)
    if kind not in ['payments','expenses','commission_receipts']:raise ValidationError('This record does not accept proof files.')
    obj=record(request,kind,pk,True)
    if kind=='payments' and obj.status not in ['PENDING','RECEIVED'] or kind=='expenses' and obj.status!='PENDING':raise ValidationError('Proof files are locked after verification or cancellation.')
    raw,mime=valid_file(request.FILES.get('file'));field={'payments':'payment','expenses':'expense','commission_receipts':'commission_receipt'}[kind]
    item=FinanceFile.objects.create(**{field:obj},filename=Path(request.FILES['file'].name).name[:255],mime=mime,content=raw,checksum=hashlib.sha256(raw).hexdigest(),uploaded_by=request.user)
    history(request,kind,obj,'PROOF_UPLOADED',new={'file_id':item.pk,'checksum':item.checksum})
    return Response({'id':item.pk,'filename':item.filename},status=201)


@extend_schema(responses=OpenApiTypes.BINARY)
@api_view(['GET'])
def file_download(request,pk):
    access(request);item=get_object_or_404(FinanceFile,pk=pk)
    kind='payments' if item.payment_id else 'expenses' if item.expense_id else 'commission_receipts';owner=item.payment_id or item.expense_id or item.commission_receipt_id;record(request,kind,owner)
    response=HttpResponse(bytes(item.content),content_type=item.mime);response['Content-Disposition']='attachment; filename="proof-'+str(item.pk)+('.pdf' if item.mime=='application/pdf' else '.png' if item.mime=='image/png' else '.jpg')+'"';response['Cache-Control']='no-store';response['X-Content-Type-Options']='nosniff';return response


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
def events(request,kind,pk):
    record(request,kind,pk)
    return Response({'results':list(FinanceEvent.objects.filter(object_kind=kind,object_id=str(pk)).order_by('-created_at').values())})


@extend_schema(responses=OpenApiTypes.BINARY)
@api_view(['GET'])
def export(request,kind):
    access(request)
    if kind not in KINDS:raise ValidationError('Unknown export section.')
    def generate():
        objects=iter(selected(request,kind).iterator(chunk_size=200))
        first=next(objects,None);initial=serialize(kind,first) if first else {'id':''}
        columns=[key for key,value in initial.items() if not isinstance(value,(dict,list))]
        class Echo:
            def write(self,value):return value
        writer=csv.writer(Echo());yield '\ufeff'+writer.writerow(columns)
        def row(data):
            values=[]
            for key in columns:
                value='' if data.get(key) is None else str(data.get(key,''))
                values.append("'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else value)
            return writer.writerow(values)
        if first:yield row(initial)
        for obj in objects:yield row(serialize(kind,obj))
    response=StreamingHttpResponse(generate(),content_type='text/csv; charset=utf-8');response['Content-Disposition']=f'attachment; filename="consman-{kind}.csv"';response['Cache-Control']='no-store';return response



@extend_schema(responses=OpenApiTypes.BINARY)
@api_view(['GET'])
def pdf(request,kind,pk):
    if kind not in ['invoices','receipts']:raise ValidationError('Choose an invoice or receipt.')
    obj=record(request,kind,pk)
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image
    from xml.sax.saxutils import escape
    buffer=io.BytesIO();styles=getSampleStyleSheet();parts=[Paragraph('The Blessing Edu · ConsMan',styles['Title']),Spacer(1,18),Paragraph(('Invoice' if kind=='invoices' else 'Payment receipt')+' '+escape(obj.reference),styles['Heading1'])]
    logo=Path(__file__).resolve().parents[1]/'qr/assets/logo_ConsMan.jpg'
    if logo.exists():parts.insert(0,Image(str(logo),width=120,height=65,kind='proportional'))
    if kind=='invoices':
        paid=invoice_paid(obj);values=[('Student',obj.issued_snapshot.get('person_name',obj.person.full_name)),('Person reference',obj.issued_snapshot.get('person_reference',obj.person.ref)),('Status',obj.status),('Issued on',obj.issued_snapshot.get('issued_on','Draft')),('Description',obj.description),('Amount',f'{obj.currency} {obj.amount}'),('Verified payments',f'{obj.currency} {paid}'),('Balance',f'{obj.currency} {obj.amount-paid}'),('Due date',str(obj.due_on or 'Not set'))]
    else:
        snap=obj.snapshot;values=[('Student',snap.get('person_name','')),('Person reference',snap.get('person_reference','')),('Payment',snap.get('payment_reference','')),('Amount',f"{snap.get('currency','')} {snap.get('amount','')}"),('Paid on',snap.get('paid_on','')),('Method',str(snap.get('method','')).replace('_',' ').title()),('Transaction reference',snap.get('transaction_reference','')),('Current payment status',obj.payment.status)]
        if obj.payment.status!='VERIFIED':parts.append(Paragraph('REFUNDED / NO LONGER VERIFIED',styles['Heading2']))
    table=Table([[Paragraph(escape(str(k)),styles['BodyText']),Paragraph(escape(str(v)),styles['BodyText'])] for k,v in values],colWidths=[150,330]);table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),10),('LINEBELOW',(0,0),(-1,-1),0.3,colors.lightgrey)]));parts.append(table)
    SimpleDocTemplate(buffer,title=obj.reference,author='The Blessing Edu').build(parts)
    response=HttpResponse(buffer.getvalue(),content_type='application/pdf');response['Content-Disposition']=f'attachment; filename="{obj.reference}.pdf"';response['Cache-Control']='no-store';return response
