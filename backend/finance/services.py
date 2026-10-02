from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from .models import Invoice, PaymentAllocation
from progression.models import Payment


def commission_amount(rule,base_amount,currency):
    base=Decimal(str(base_amount))
    if not base.is_finite() or base<0:raise ValidationError('Commission base must be a nonnegative amount.')
    if currency!=rule.currency:raise ValidationError('Use the rule currency; currency conversion is not assumed.')
    value=Decimal(str(rule.value))
    if not value.is_finite() or value<0:raise ValidationError('Commission rule must contain a nonnegative amount.')
    if rule.method=='FIXED':total=value
    elif rule.method=='PERCENTAGE':
        if not 0<=value<=100:raise ValidationError('Commission percentage must be between 0 and 100.')
        total=base*value/Decimal(100)
    else:raise ValidationError('Unknown commission calculation method.')
    return total.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)


@transaction.atomic
def allocate_payment(invoice_id,payment_id,amount,actor):
    invoice=Invoice.objects.select_for_update().get(pk=invoice_id)
    payment=Payment.objects.select_for_update().get(pk=payment_id)
    amount=Decimal(str(amount))
    if not amount.is_finite() or amount<=0 or amount!=amount.quantize(Decimal('0.01')):raise ValidationError('Allocation requires a positive amount with at most two decimals.')
    if invoice.status!='ISSUED':raise ValidationError('Only issued invoices accept payments.')
    if payment.status!='VERIFIED':raise ValidationError('Verify payment before allocating it.')
    if invoice.person_id!=payment.person_id or invoice.currency!=payment.currency:raise ValidationError('Invoice and payment must belong to the same person and currency.')
    if invoice.application_id and invoice.application_id!=payment.application_id:raise ValidationError('Payment must match the invoice application.')
    allocated=payment.allocations.aggregate(total=Sum('amount'))['total'] or Decimal(0)
    settled=invoice.allocations.filter(payment__status='VERIFIED').aggregate(total=Sum('amount'))['total'] or Decimal(0)
    if allocated+amount>payment.amount or settled+amount>invoice.amount:raise ValidationError('Allocation exceeds the payment balance or invoice balance.')
    if PaymentAllocation.objects.filter(invoice=invoice,payment=payment).exists():raise ValidationError('This payment is already allocated to the invoice.')
    return PaymentAllocation.objects.create(invoice=invoice,payment=payment,amount=amount,created_by=actor)
