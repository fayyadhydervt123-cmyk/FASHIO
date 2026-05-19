from types import SimpleNamespace
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from products.models import Cart, ProductVariant
from user.models import Address


MAX_QUANTITY_PER_ORDER = 5


@login_required(login_url='user_login')
def checkout_page(request):
    source = request.GET.get('source', 'cart')

    checkout_items = []
    subtotal = 0
    total_items = 0

    if source == 'cart':
        request.session.pop('buy_now', None)
        request.session.modified = True

    # BUY NOW CHECKOUT
    if source == 'buy_now':
        buy_now_data = request.session.get('buy_now')

        if not buy_now_data:
            messages.error(request, 'Buy now session expired.')
            return redirect('product_list')

        variant_id = buy_now_data.get('variant_id')

        try:
            quantity = int(buy_now_data.get('quantity', 1))
        except (TypeError, ValueError):
            messages.error(request, 'Invalid quantity.')
            return redirect('product_list')

        variant = get_object_or_404(
            ProductVariant.objects.select_related(
                'product',
                'product__subcategory',
                'product__subcategory__category'
            ).prefetch_related('images'),
            id=variant_id,
            status='ACTIVE',
            product__is_active=True,
            product__subcategory__isnull=False,
            product__subcategory__category__is_blocked=False
        )

        if quantity < 1:
            messages.error(request, 'Invalid quantity.')
            return redirect('product_detail', product_id=variant.product.id)

        max_allowed = min(variant.stock, MAX_QUANTITY_PER_ORDER)

        if max_allowed <= 0:
            messages.error(request, 'This product is out of stock.')
            return redirect('product_detail', product_id=variant.product.id)

        if quantity > max_allowed:
            messages.error(request, f'Only {max_allowed} item(s) available.')
            return redirect('product_detail', product_id=variant.product.id)

        first_image = variant.images.first()
        thumbnail = first_image.image.url if first_image else None

        unit_price = variant.discounted_price
        item_subtotal = unit_price * quantity

        checkout_item = SimpleNamespace(
            product=variant.product,
            variant=variant,
            quantity=quantity,
            thumbnail=thumbnail,
            unit_price_amount=unit_price,
            subtotal_amount=item_subtotal,
        )

        checkout_items.append(checkout_item)

        subtotal += item_subtotal
        total_items += quantity

        checkout_source = 'buy_now'

    # CART CHECKOUT
    else:
        cart_items = (
            Cart.objects
            .filter(user=request.user)
            .select_related(
                'product',
                'variant',
                'product__subcategory',
                'product__subcategory__category'
            )
            .prefetch_related('variant__images')
            .order_by('-created_at')
        )

        if not cart_items.exists():
            messages.error(request, 'Your cart is empty.')
            return redirect('cart_page')

        for item in cart_items:
            product_available = (
                item.product.is_active and
                item.product.subcategory is not None and
                item.product.subcategory.category.is_blocked is False and
                item.variant.status == 'ACTIVE' and
                item.variant.stock > 0
            )

            max_allowed = min(item.variant.stock, MAX_QUANTITY_PER_ORDER)

            if not product_available:
                messages.error(request, 'Please remove unavailable products before checkout.')
                return redirect('cart_page')

            if item.quantity > max_allowed:
                messages.error(request, f'Only {max_allowed} item(s) available for {item.product.name}.')
                return redirect('cart_page')

            first_image = item.variant.images.first()
            item.thumbnail = first_image.image.url if first_image else None

            item.unit_price_amount = item.variant.discounted_price
            item.subtotal_amount = item.variant.discounted_price * item.quantity

            subtotal += item.subtotal_amount
            total_items += item.quantity

            checkout_items.append(item)

        checkout_source = 'cart'

    delivery_fee = 0
    tax_amount = 0
    discount_amount = 0
    total_payable = subtotal + delivery_fee + tax_amount - discount_amount

    addresses = (
        Address.objects
        .filter(user=request.user)
        .order_by('-created_at')
    )

    selected_address = addresses.first()

    return render(request, 'checkout/checkout.html', {
        'cart_items': checkout_items,
        'subtotal': subtotal,
        'delivery_fee': delivery_fee,
        'tax_amount': tax_amount,
        'discount_amount': discount_amount,
        'total_payable': total_payable,
        'total_items': total_items,
        'addresses': addresses,
        'selected_address': selected_address,
        'checkout_source': checkout_source,
    })