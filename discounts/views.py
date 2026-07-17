from django.shortcuts import render
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.views.decorators.cache import never_cache
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from decimal import Decimal, InvalidOperation
from datetime import date
from .models import Offer, Coupon
from products.models import Product, Category, SubCategory


def is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


# ─────────────────────────────── OFFERS ───────────────────────────────

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def offer_list(request):
    query = request.GET.get('q', '').strip()
    type_filter = request.GET.get('type', '').strip()
    status_filter = request.GET.get('status', '').strip()
    sort_by = request.GET.get('sort', 'newest').strip()

    offers = Offer.objects.select_related('product', 'category').all()

    if query:
        offers = offers.filter(
            Q(title__icontains=query) |
            Q(product__name__icontains=query) |
            Q(category__name__icontains=query)
        )

    if type_filter:
        offers = offers.filter(offer_type=type_filter)

    if sort_by == "oldest":
        offers = offers.order_by("created_at")
    elif sort_by == "start_date":
        offers = offers.order_by("start_date")
    elif sort_by == "end_date":
        offers = offers.order_by("end_date")
    else:
        offers = offers.order_by("-created_at")

    # Stats (counted before status-filtering so cards stay stable)
    all_offers = list(Offer.objects.all())
    total_offers = len(all_offers)
    active_count = sum(1 for o in all_offers if o.computed_status == "ACTIVE")
    expired_count = sum(1 for o in all_offers if o.computed_status == "EXPIRED")
    upcoming_count = sum(1 for o in all_offers if o.computed_status == "UPCOMING")

    # Status filter applied after computed_status is available (Python-side, since it's a property not a DB field)
    if status_filter:
        offers = [o for o in offers if o.computed_status == status_filter]

    paginator = Paginator(offers, 10)
    page_number = request.GET.get('page')
    offers_page = paginator.get_page(page_number)

    categories = Category.objects.filter(is_blocked=False).order_by('name')
    products = Product.objects.filter(is_active=True).order_by('name')
    subcategories = SubCategory.objects.select_related('category').order_by('category__name', 'name')

    return render(request, 'offers/offers.html', {
        'offers': offers_page,
        'query': query,
        'type_filter': type_filter,
        'status_filter': status_filter,
        'sort_by': sort_by,

        'total_offers': total_offers,
        'active_count': active_count,
        'expired_count': expired_count,
        'upcoming_count': upcoming_count,

        'categories': categories,
        'products': products,
        'subcategories': subcategories
    })


def _validate_offer_form(request, exclude_id=None):
    """Shared validation for add/edit. Returns (errors dict, cleaned data dict)."""
    errors = {}

    title = request.POST.get('title', '').strip()
    offer_type = request.POST.get('offer_type', '').strip()
    discount_type = request.POST.get('discount_type', '').strip()
    discount_value = request.POST.get('discount_value', '').strip()
    target_id = request.POST.get('target_id', '').strip()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()

    if not title:
        errors['title'] = 'Offer title is required.'
    elif len(title) < 3:
        errors['title'] = 'Title must be at least 3 characters.'

    if offer_type not in ['PRODUCT', 'SUBCATEGORY', 'CATEGORY']:
        errors['offer_type'] = 'Please select a valid offer type.'

    if discount_type not in ['PERCENTAGE', 'FLAT']:
        errors['discount_type'] = 'Please select a valid discount type.'

    discount_value_val = None
    if not discount_value:
        errors['discount_value'] = 'Discount value is required.'
    else:
        try:
            discount_value_val = Decimal(discount_value)
            if discount_value_val <= 0:
                errors['discount_value'] = 'Discount must be greater than 0.'
            elif discount_type == 'PERCENTAGE' and discount_value_val > 100:
                errors['discount_value'] = 'Percentage cannot exceed 100.'
        except InvalidOperation:
            errors['discount_value'] = 'Enter a valid number.'

    product = None
    category = None
    subcategory = None

    if not target_id:
        errors['target_id'] = 'Please select a target.'
    elif offer_type == 'PRODUCT':
        try:
            product = Product.objects.get(id=target_id)
        except Product.DoesNotExist:
            errors['target_id'] = 'Selected product not found.'
    elif offer_type == 'CATEGORY':
        try:
            category = Category.objects.get(id=target_id)
        except Category.DoesNotExist:
            errors['target_id'] = 'Selected category not found.'
    elif offer_type == 'SUBCATEGORY':
        try:
            subcategory = SubCategory.objects.get(id=target_id)
        except SubCategory.DoesNotExist:
            errors['target_id'] = 'Selected subcategory not found.'

    # FLAT discount must not equal/exceed the product's price
    if (
        offer_type == 'PRODUCT'
        and product is not None
        and discount_type == 'FLAT'
        and discount_value_val is not None
        and 'discount_value' not in errors
    ):
        if discount_value_val >= product.base_price:
            errors['discount_value'] = (
                f'Flat discount (₹{discount_value_val}) cannot be greater than '
                f'or equal to the product price (₹{product.base_price}).'
            )

    start_date_val = None
    end_date_val = None

    if not start_date:
        errors['start_date'] = 'Start date is required.'
    if not end_date:
        errors['end_date'] = 'End date is required.'

    if start_date and end_date and 'start_date' not in errors and 'end_date' not in errors:
        try:
            start_date_val = date.fromisoformat(start_date)
            end_date_val = date.fromisoformat(end_date)
            if end_date_val < start_date_val:
                errors['end_date'] = 'End date cannot be before start date.'
        except ValueError:
            errors['start_date'] = 'Invalid date format.'

    # Overlap check: same target can't have two offers with overlapping date ranges
    if (
        'target_id' not in errors
        and start_date_val is not None
        and end_date_val is not None
        and offer_type in ['PRODUCT', 'SUBCATEGORY', 'CATEGORY']
    ):
        overlap_qs = Offer.objects.filter(
            offer_type=offer_type,
            start_date__lte=end_date_val,
            end_date__gte=start_date_val,
        )

        if offer_type == 'PRODUCT':
            overlap_qs = overlap_qs.filter(product=product)
        elif offer_type == 'CATEGORY':
            overlap_qs = overlap_qs.filter(category=category)
        elif offer_type == 'SUBCATEGORY':
            overlap_qs = overlap_qs.filter(subcategory=subcategory)

        if exclude_id:
            overlap_qs = overlap_qs.exclude(id=exclude_id)

        if overlap_qs.exists():
            errors['target_id'] = (
                'An active or upcoming offer already exists for this target '
                'in the selected date range.'
            )

    cleaned = {
        'title': title,
        'offer_type': offer_type,
        'discount_type': discount_type,
        'discount_value': discount_value_val,
        'product': product,
        'subcategory': subcategory,
        'category': category,
        'start_date': start_date_val,
        'end_date': end_date_val,
    }

    return errors, cleaned


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def add_offer(request):
    if request.method == 'POST':
        errors, cleaned = _validate_offer_form(request)

        if errors:
            for field, msg in errors.items():
                messages.error(request, msg)
            return redirect('offer_list')

        Offer.objects.create(
            title=cleaned['title'],
            offer_type=cleaned['offer_type'],
            discount_type=cleaned['discount_type'],
            discount_value=cleaned['discount_value'],
            product=cleaned['product'],
            subcategory=cleaned['subcategory'],
            category=cleaned['category'],
            start_date=cleaned['start_date'],
            end_date=cleaned['end_date'],
        )

        messages.success(request, f'Offer "{cleaned["title"]}" created successfully.')
        return redirect('offer_list')

    return redirect('offer_list')


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def edit_offer(request, offer_id):
    offer = get_object_or_404(Offer, id=offer_id)

    if request.method == 'POST':
        errors, cleaned = _validate_offer_form(request, exclude_id=offer_id)

        if errors:
            for field, msg in errors.items():
                messages.error(request, msg)
            return redirect('offer_list')

        offer.title = cleaned['title']
        offer.offer_type = cleaned['offer_type']
        offer.discount_type = cleaned['discount_type']
        offer.discount_value = cleaned['discount_value']
        offer.product = cleaned['product']
        offer.subcategory = cleaned['subcategory']
        offer.category = cleaned['category']
        offer.start_date = cleaned['start_date']
        offer.end_date = cleaned['end_date']
        offer.save()

        messages.success(request, f'Offer "{offer.title}" updated successfully.')
        return redirect('offer_list')

    return redirect('offer_list')


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def delete_offer(request, offer_id):
    if request.method == 'POST':
        offer = get_object_or_404(Offer, id=offer_id)
        title = offer.title
        offer.delete()
        messages.success(request, f'Offer "{title}" deleted successfully.')
        return redirect('offer_list')

    return redirect('offer_list')



# ─────────────────────────────── COUPONS ───────────────────────────────

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def coupon_list(request):
    query = request.GET.get('q', '').strip()
    discount_type_filter = request.GET.get('discount_type', '').strip()
    status_filter = request.GET.get('status', '').strip()
    sort_by = request.GET.get('sort', 'newest').strip()

    coupons = Coupon.objects.all()

    if query:
        coupons = coupons.filter(Q(code__icontains=query))

    if discount_type_filter:
        coupons = coupons.filter(discount_type=discount_type_filter)

    if sort_by == "oldest":
        coupons = coupons.order_by("created_at")
    elif sort_by == "start_date":
        coupons = coupons.order_by("start_date")
    elif sort_by == "end_date":
        coupons = coupons.order_by("end_date")
    else:
        coupons = coupons.order_by("-created_at")

    # Stats (counted before status-filtering so cards stay stable)
    all_coupons = list(Coupon.objects.all())
    total_coupons = len(all_coupons)
    active_count = sum(1 for c in all_coupons if c.computed_status == "ACTIVE")
    expired_count = sum(1 for c in all_coupons if c.computed_status == "EXPIRED")
    upcoming_count = sum(1 for c in all_coupons if c.computed_status == "UPCOMING")

    # Status filter applied after computed_status is available (Python-side, since it's a property not a DB field)
    if status_filter:
        coupons = [c for c in coupons if c.computed_status == status_filter]

    paginator = Paginator(coupons, 10)
    page_number = request.GET.get('page')
    coupons_page = paginator.get_page(page_number)

    return render(request, 'coupons/coupons.html', {
        'coupons': coupons_page,
        'query': query,
        'discount_type_filter': discount_type_filter,
        'status_filter': status_filter,
        'sort_by': sort_by,

        'total_coupons': total_coupons,
        'active_count': active_count,
        'expired_count': expired_count,
        'upcoming_count': upcoming_count,
    })


def _validate_coupon_form(request, exclude_id=None):
    """Shared validation for add/edit. Returns (errors dict, cleaned data dict)."""
    errors = {}

    code = request.POST.get('code', '').strip().upper()
    discount_type = request.POST.get('discount_type', '').strip()
    discount_value = request.POST.get('discount_value', '').strip()
    max_discount_amount = request.POST.get('max_discount_amount', '').strip()
    min_order_value = request.POST.get('min_order_value', '').strip()
    usage_limit_global = request.POST.get('usage_limit_global', '').strip()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()
    is_active = request.POST.get('is_active') == 'on'

    if not code:
        errors['code'] = 'Coupon code is required.'
    elif len(code) < 3:
        errors['code'] = 'Coupon code must be at least 3 characters.'
    else:
        existing = Coupon.objects.filter(code=code)
        if exclude_id:
            existing = existing.exclude(id=exclude_id)
        if existing.exists():
            errors['code'] = 'A coupon with this code already exists.'

    if discount_type not in ['PERCENTAGE', 'FLAT']:
        errors['discount_type'] = 'Please select a valid discount type.'

    discount_value_val = None
    if not discount_value:
        errors['discount_value'] = 'Discount value is required.'
    else:
        try:
            discount_value_val = Decimal(discount_value)
            if discount_value_val <= 0:
                errors['discount_value'] = 'Discount must be greater than 0.'
            elif discount_type == 'PERCENTAGE' and discount_value_val > 100:
                errors['discount_value'] = 'Percentage cannot exceed 100.'
        except InvalidOperation:
            errors['discount_value'] = 'Enter a valid number.'

    # max_discount_amount only makes sense for PERCENTAGE coupons
    max_discount_amount_val = None
    if discount_type == 'FLAT' and max_discount_amount:
        errors['max_discount_amount'] = 'Max discount cap only applies to percentage coupons.'
    elif max_discount_amount:
        try:
            max_discount_amount_val = Decimal(max_discount_amount)
            if max_discount_amount_val <= 0:
                errors['max_discount_amount'] = 'Max discount must be greater than 0.'
        except InvalidOperation:
            errors['max_discount_amount'] = 'Enter a valid number.'

    min_order_value_val = Decimal('0.00')
    if min_order_value:
        try:
            min_order_value_val = Decimal(min_order_value)
            if min_order_value_val < 0:
                errors['min_order_value'] = 'Minimum purchase amount cannot be negative.'
        except InvalidOperation:
            errors['min_order_value'] = 'Enter a valid number.'

    usage_limit_global_val = None
    if usage_limit_global:
        try:
            usage_limit_global_val = int(usage_limit_global)
            if usage_limit_global_val <= 0:
                errors['usage_limit_global'] = 'Usage limit must be greater than 0.'
        except ValueError:
            errors['usage_limit_global'] = 'Enter a valid whole number.'

    # Can't set a usage limit below what's already been used
    if (
        exclude_id
        and usage_limit_global_val is not None
        and 'usage_limit_global' not in errors
    ):
        existing_coupon = Coupon.objects.filter(id=exclude_id).first()
        if existing_coupon and usage_limit_global_val < existing_coupon.times_used:
            errors['usage_limit_global'] = (
                f'Usage limit cannot be less than times already used '
                f'({existing_coupon.times_used}).'
            )

    start_date_val = None
    end_date_val = None

    if not start_date:
        errors['start_date'] = 'Start date is required.'
    if not end_date:
        errors['end_date'] = 'End date is required.'

    if start_date and end_date and 'start_date' not in errors and 'end_date' not in errors:
        try:
            start_date_val = date.fromisoformat(start_date)
            end_date_val = date.fromisoformat(end_date)
            if end_date_val < start_date_val:
                errors['end_date'] = 'End date cannot be before start date.'
        except ValueError:
            errors['start_date'] = 'Invalid date format.'

    cleaned = {
        'code': code,
        'discount_type': discount_type,
        'discount_value': discount_value_val,
        'max_discount_amount': max_discount_amount_val,
        'min_order_value': min_order_value_val,
        'usage_limit_global': usage_limit_global_val,
        'start_date': start_date_val,
        'end_date': end_date_val,
        'is_active': is_active,
    }

    return errors, cleaned


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def add_coupon(request):
    if request.method == 'POST':
        errors, cleaned = _validate_coupon_form(request)

        if errors:
            for field, msg in errors.items():
                messages.error(request, msg)
            return redirect('coupon_list')

        Coupon.objects.create(
            code=cleaned['code'],
            discount_type=cleaned['discount_type'],
            discount_value=cleaned['discount_value'],
            max_discount_amount=cleaned['max_discount_amount'],
            min_order_value=cleaned['min_order_value'],
            usage_limit_global=cleaned['usage_limit_global'],
            start_date=cleaned['start_date'],
            end_date=cleaned['end_date'],
            is_active=cleaned['is_active'],
        )

        messages.success(request, f'Coupon "{cleaned["code"]}" created successfully.')
        return redirect('coupon_list')

    return redirect('coupon_list')


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def edit_coupon(request, coupon_id):
    coupon = get_object_or_404(Coupon, id=coupon_id)

    if request.method == 'POST':
        errors, cleaned = _validate_coupon_form(request, exclude_id=coupon_id)

        if errors:
            for field, msg in errors.items():
                messages.error(request, msg)
            return redirect('coupon_list')

        coupon.code = cleaned['code']
        coupon.discount_type = cleaned['discount_type']
        coupon.discount_value = cleaned['discount_value']
        coupon.max_discount_amount = cleaned['max_discount_amount']
        coupon.min_order_value = cleaned['min_order_value']
        coupon.usage_limit_global = cleaned['usage_limit_global']
        coupon.start_date = cleaned['start_date']
        coupon.end_date = cleaned['end_date']
        coupon.is_active = cleaned['is_active']
        coupon.save()

        messages.success(request, f'Coupon "{coupon.code}" updated successfully.')
        return redirect('coupon_list')

    return redirect('coupon_list')


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def delete_coupon(request, coupon_id):
    if request.method == 'POST':
        coupon = get_object_or_404(Coupon, id=coupon_id)
        code = coupon.code
        coupon.delete()
        messages.success(request, f'Coupon "{code}" deleted successfully.')
        return redirect('coupon_list')

    return redirect('coupon_list')