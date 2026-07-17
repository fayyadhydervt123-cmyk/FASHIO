import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from types import SimpleNamespace

import razorpay
from weasyprint import HTML

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from discounts.models import Coupon, CouponUsage
from products.models import Cart, Category, Product, ProductVariant
from user.models import Address, Wallet, WalletTransaction

from .models import Order, OrderItem, OrderStatusHistory, Payment, ReturnRequest

MAX_QUANTITY_PER_ORDER = 5

GST_RATE = Decimal("0.05")  # 5% GST inclusive

REFERRAL_REWARD_REFERRER = Decimal("200.00")
REFERRAL_REWARD_REFERRED = Decimal("100.00")

BILLABLE_ITEM_STATUSES = ["ACTIVE", "RETURN_REQUESTED", "RETURN_APPROVED"]

CANCELLATION_REASONS = [
    ("ordered_by_mistake", "Ordered by mistake"),
    ("changed_mind", "Changed my mind"),
    ("wrong_size_or_color", "Need to change size or color"),
    ("wrong_address", "Need to change delivery address"),
    ("delivery_too_late", "Delivery time is too long"),
    ("found_better_price", "Found a better price elsewhere"),
    ("duplicate_order", "Placed duplicate order"),
    ("payment_issue", "Payment issue"),
    ("no_longer_needed", "Product no longer needed"),
    ("other", "Other"),
]

RETURN_REASONS = [
    ("wrong_size_or_fit", "Wrong size or fit"),
    ("damaged_or_defective", "Damaged or defective item"),
    ("different_from_description", "Item is different from description"),
    ("wrong_item_received", "Wrong item received"),
    ("quality_not_expected", "Quality not as expected"),
    ("changed_mind", "Changed my mind"),
    ("ordered_by_mistake", "Ordered by mistake"),
    ("late_delivery", "Delivered too late"),
    ("missing_parts_or_tags", "Missing parts or tags"),
    ("other", "Other"),
]


def apply_referral_reward(order):
    """
    Credit referral bonuses on a user's first order.

    Referrer gets ₹200, the referred user gets ₹100.
    """
    user = order.user

    if not user.referred_by or user.referral_reward_given:
        return

    # Must be exactly their first order
    if Order.objects.filter(user=user).count() != 1:
        return

    referrer = user.referred_by

    # Reward the referrer
    referrer_wallet, _ = Wallet.objects.get_or_create(user=referrer)
    referrer_wallet.balance += REFERRAL_REWARD_REFERRER
    referrer_wallet.save(update_fields=["balance", "updated_at"])

    WalletTransaction.objects.create(
        wallet=referrer_wallet,
        amount=REFERRAL_REWARD_REFERRER,
        transaction_type="CREDIT",
        purpose="REFERRAL_BONUS",
        order_id=str(order.order_id),
        description=f"Referral bonus — {user.fullname} placed their first order.",
    )

    # Reward the referred user
    referred_wallet, _ = Wallet.objects.get_or_create(user=user)
    referred_wallet.balance += REFERRAL_REWARD_REFERRED
    referred_wallet.save(update_fields=["balance", "updated_at"])

    WalletTransaction.objects.create(
        wallet=referred_wallet,
        amount=REFERRAL_REWARD_REFERRED,
        transaction_type="CREDIT",
        purpose="REFERRAL_BONUS",
        order_id=str(order.order_id),
        description="Welcome bonus for signing up with a referral code.",
    )

    user.referral_reward_given = True
    user.save(update_fields=["referral_reward_given"])


def calculate_checkout_totals(subtotal, coupon_discount=Decimal("0.00")):
    """Return delivery fee, GST breakdown, and final payable total for checkout."""
    delivery_fee = Decimal("0.00")

    gst_amount = (subtotal - (subtotal / (1 + GST_RATE))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    total_payable = (subtotal + delivery_fee - coupon_discount).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return {
        "delivery_fee": delivery_fee,
        "gst_rate": GST_RATE * 100,  # passes as 5 for display
        "gst_amount": gst_amount,
        "coupon_discount": coupon_discount,
        "total_payable": total_payable,
    }


def get_item_refund_amount(order, item):
    """
    Return (refund_amount, offer_savings, coupon_share) for a single OrderItem.

    refund_amount = actual amount the customer paid for this item
                     (item.subtotal, already net of any product/category Offer,
                      minus this item's proportional share of any order-level Coupon)
    offer_savings = amount saved via product/category Offer on this item (display only)
    coupon_share  = this item's proportional share of the order's coupon discount
                    (display only)
    """
    offer_savings = Decimal("0.00")
    if item.original_price is not None and item.original_price > item.price:
        offer_savings = (item.original_price - item.price) * item.quantity

    if not order.subtotal or order.subtotal == 0:
        return item.subtotal, offer_savings, Decimal("0.00")

    coupon_discount = Decimal("0.00")
    if order.coupon:
        usage = order.coupon_usage.first()
        if usage:
            coupon_discount = usage.discount_amount

    if coupon_discount <= 0:
        return item.subtotal, offer_savings, Decimal("0.00")

    coupon_share = (item.subtotal / order.subtotal) * coupon_discount
    coupon_share = coupon_share.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    refund_amount = max(item.subtotal - coupon_share, Decimal("0.00"))

    return refund_amount, offer_savings, coupon_share


def validate_coupon(code, user, subtotal):
    """Return (coupon, discount_amount, error_message)."""
    code = code.strip().upper()

    if not code:
        return None, Decimal("0.00"), "Please enter a coupon code."

    try:
        coupon = Coupon.objects.get(code=code)
    except Coupon.DoesNotExist:
        return None, Decimal("0.00"), "Invalid coupon code."

    if coupon.computed_status != "ACTIVE":
        return None, Decimal("0.00"), "This coupon is not active."

    if subtotal < coupon.min_order_value:
        return (
            None,
            Decimal("0.00"),
            f"Minimum order value for this coupon is ₹{coupon.min_order_value}.",
        )

    if (
        coupon.usage_limit_global is not None
        and coupon.times_used >= coupon.usage_limit_global
    ):
        return None, Decimal("0.00"), "This coupon has reached its usage limit."

    already_used = CouponUsage.objects.filter(coupon=coupon, user=user).count()
    if already_used >= coupon.usage_limit_per_user:
        return None, Decimal("0.00"), "You have already used this coupon."

    discount = coupon.apply_to(subtotal)
    return coupon, discount, None


def get_available_coupons(user, subtotal):
    """Return a list of dicts: {coupon, is_applicable, reason, discount_preview}."""
    coupons = Coupon.objects.filter(is_active=True).order_by("-created_at")

    result = []

    for coupon in coupons:
        if coupon.computed_status != "ACTIVE":
            continue

        is_applicable = True
        reason = None

        if subtotal < coupon.min_order_value:
            is_applicable = False
            shortfall = coupon.min_order_value - subtotal
            reason = f"Add ₹{shortfall:.0f} more to unlock"
        else:
            already_used = CouponUsage.objects.filter(coupon=coupon, user=user).count()
            if already_used >= coupon.usage_limit_per_user:
                is_applicable = False
                reason = "Already used"

        discount_preview = coupon.apply_to(subtotal) if is_applicable else Decimal("0.00")

        result.append(
            {
                "coupon": coupon,
                "is_applicable": is_applicable,
                "reason": reason,
                "discount_preview": discount_preview,
            }
        )

    return result


# Handles two purchase flows in one view
@login_required(login_url="user_login")
def checkout_page(request):
    source = request.GET.get("source", "cart")

    cart_items = []
    subtotal = Decimal("0.00")
    total_items = 0

    if source == "buy_now":
        buy_now_data = request.session.get("buy_now")

        if not buy_now_data:
            messages.error(request, "Buy now session expired.")
            return redirect("product_list")

        variant = get_object_or_404(
            ProductVariant.objects.select_related("product").prefetch_related("images"),
            id=buy_now_data.get("variant_id"),
            status="ACTIVE",
        )

        quantity = int(buy_now_data.get("quantity", 1))

        first_image = variant.images.first()
        thumbnail = first_image.image.url if first_image else None

        unit_price = variant.discounted_price
        original_unit_price = variant.price
        item_subtotal = unit_price * quantity
        item_savings = (original_unit_price - unit_price) * quantity

        checkout_item = SimpleNamespace(
            product=variant.product,
            variant=variant,
            quantity=quantity,
            thumbnail=thumbnail,
            unit_price_amount=unit_price,
            original_unit_price=original_unit_price,
            subtotal_amount=item_subtotal,
            savings_amount=item_savings,
        )

        cart_items.append(checkout_item)

        subtotal += item_subtotal
        total_items += quantity
        checkout_source = "buy_now"

    else:
        cart_queryset = (
            Cart.objects.filter(user=request.user)
            .select_related("product", "variant")
            .prefetch_related("variant__images")
        )

        if not cart_queryset.exists():
            messages.error(request, "Your cart is empty.")
            return redirect("cart_page")

        for item in cart_queryset:
            first_image = item.variant.images.first()
            item.thumbnail = first_image.image.url if first_image else None

            item.unit_price_amount = item.variant.discounted_price
            item.original_unit_price = item.variant.price
            item.subtotal_amount = item.variant.discounted_price * item.quantity
            item.savings_amount = (
                item.variant.price - item.variant.discounted_price
            ) * item.quantity

            subtotal += item.subtotal_amount
            total_items += item.quantity

            cart_items.append(item)

        checkout_source = "cart"

    total_savings = sum(item.savings_amount for item in cart_items)

    addresses = Address.objects.filter(user=request.user).order_by("-created_at")
    selected_address = addresses.first()

    applied_coupon_session = request.session.get("applied_coupon")
    coupon = None
    coupon_discount = Decimal("0.00")

    if applied_coupon_session:
        coupon, coupon_discount, error = validate_coupon(
            applied_coupon_session["code"], request.user, subtotal
        )
        if error:
            request.session.pop("applied_coupon", None)
            request.session.modified = True
            coupon = None
            coupon_discount = Decimal("0.00")

    available_coupons = (
        get_available_coupons(request.user, subtotal) if subtotal > 0 else []
    )

    have_applicable_coupon = any(entry["is_applicable"] for entry in available_coupons)

    applicable_coupon_count = sum(
        1 for entry in available_coupons if entry["is_applicable"]
    )

    totals = calculate_checkout_totals(subtotal, coupon_discount)

    delivery_fee = totals["delivery_fee"]
    gst_amount = totals["gst_amount"]
    gst_rate = totals["gst_rate"]
    total_payable = totals["total_payable"]

    return render(
        request,
        "checkout/checkout.html",
        {
            "cart_items": cart_items,
            "addresses": addresses,
            "selected_address": selected_address,
            "subtotal": subtotal,
            "gst_amount": gst_amount,
            "gst_rate": gst_rate,
            "delivery_fee": delivery_fee,
            "total_payable": total_payable,
            "total_savings": total_savings,
            "total_items": total_items,
            "checkout_source": checkout_source,
            "applied_coupon": coupon,
            "coupon_discount": coupon_discount,
            "available_coupons": available_coupons,
            "have_applicable_coupon": have_applicable_coupon,
            "applicable_coupon_count": applicable_coupon_count,
        },
    )


@login_required(login_url="user_login")
def payment_method(request):
    source = request.GET.get("source", "cart")
    address_id = request.GET.get("address_id")

    if not address_id:
        messages.error(request, "Please select a delivery address.")
        return redirect("checkout_page")

    selected_address = get_object_or_404(Address, id=address_id, user=request.user)

    cart_items = []
    subtotal = Decimal("0.00")
    total_items = 0

    if source == "buy_now":
        buy_now_data = request.session.get("buy_now")

        if not buy_now_data:
            messages.error(request, "Buy now session expired.")
            return redirect("product_list")

        variant = get_object_or_404(
            ProductVariant.objects.select_related("product").prefetch_related("images"),
            id=buy_now_data.get("variant_id"),
            status="ACTIVE",
        )

        quantity = int(buy_now_data.get("quantity", 1))

        first_image = variant.images.first()
        thumbnail = first_image.image.url if first_image else None

        unit_price = variant.discounted_price
        original_unit_price = variant.price
        item_subtotal = unit_price * quantity
        item_savings = (original_unit_price - unit_price) * quantity

        checkout_item = SimpleNamespace(
            product=variant.product,
            variant=variant,
            quantity=quantity,
            thumbnail=thumbnail,
            unit_price_amount=unit_price,
            original_unit_price=original_unit_price,
            subtotal_amount=item_subtotal,
            savings_amount=item_savings,
        )

        cart_items.append(checkout_item)
        subtotal += item_subtotal
        total_items += quantity

        checkout_source = "buy_now"

    else:
        cart_queryset = (
            Cart.objects.filter(user=request.user)
            .select_related("product", "variant")
            .prefetch_related("variant__images")
        )

        if not cart_queryset.exists():
            messages.error(request, "Your cart is empty.")
            return redirect("cart_page")

        for item in cart_queryset:
            first_image = item.variant.images.first()
            item.thumbnail = first_image.image.url if first_image else None

            item.unit_price_amount = item.variant.discounted_price
            item.original_unit_price = item.variant.price
            item.subtotal_amount = item.variant.discounted_price * item.quantity
            item.savings_amount = (
                item.variant.price - item.variant.discounted_price
            ) * item.quantity

            subtotal += item.subtotal_amount
            total_items += item.quantity

            cart_items.append(item)

        checkout_source = "cart"

    # ---- runs for BOTH buy_now and cart paths ----
    total_savings = sum(item.savings_amount for item in cart_items)

    applied_coupon_session = request.session.get("applied_coupon")
    coupon = None
    coupon_discount = Decimal("0.00")

    if applied_coupon_session:
        coupon, coupon_discount, error = validate_coupon(
            applied_coupon_session["code"], request.user, subtotal
        )
        if error:
            request.session.pop("applied_coupon", None)
            request.session.modified = True
            coupon = None
            coupon_discount = Decimal("0.00")

    totals = calculate_checkout_totals(subtotal, coupon_discount)

    delivery_fee = totals["delivery_fee"]
    gst_amount = totals["gst_amount"]
    gst_rate = totals["gst_rate"]
    total_payable = totals["total_payable"]

    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    wallet_balance = wallet.balance

    return render(
        request,
        "checkout/payment_method.html",
        {
            "cart_items": cart_items,
            "selected_address": selected_address,
            "subtotal": subtotal,
            "gst_amount": gst_amount,
            "gst_rate": gst_rate,
            "delivery_fee": delivery_fee,
            "total_payable": total_payable,
            "total_items": total_items,
            "checkout_source": checkout_source,
            "wallet_balance": wallet_balance,
            "total_savings": total_savings,
            "applied_coupon": coupon,
            "coupon_discount": coupon_discount,
        },
    )


@login_required(login_url="user_login")
def place_order(request):
    if request.method != "POST":
        return redirect("payment_method")

    address_id = request.POST.get("address_id")
    payment_method = request.POST.get("payment_method")
    source = request.POST.get("source", "cart")

    if not address_id:
        messages.error(request, "Please select a delivery address.")
        return redirect("checkout_page")

    if payment_method not in ["COD", "RAZORPAY", "WALLET"]:
        messages.error(request, "Please select a valid payment method.")
        return redirect("payment_method")

    address = get_object_or_404(Address, id=address_id, user=request.user)

    order_items_data = []
    subtotal = Decimal("0.00")
    total_discount = Decimal("0.00")

    if source == "buy_now":
        buy_now_data = request.session.get("buy_now")

        if not buy_now_data:
            messages.error(request, "Buy now session expired.")
            return redirect("product_list")

        variant = get_object_or_404(
            ProductVariant, id=buy_now_data.get("variant_id"), status="ACTIVE"
        )

        quantity = int(buy_now_data.get("quantity", 1))

        if variant.stock < quantity:
            messages.error(request, "Not enough stock available.")
            return redirect("product_detail", product_id=variant.product.id)

        price = variant.discounted_price
        original_price = variant.price
        item_total = price * quantity
        item_discount = (original_price - price) * quantity

        order_items_data.append(
            {
                "product": variant.product,
                "variant": variant,
                "quantity": quantity,
                "price": price,
                "original_price": original_price,
                "subtotal": item_total,
            }
        )

        subtotal += item_total
        total_discount += item_discount

    else:
        cart_items = Cart.objects.filter(user=request.user).select_related(
            "product", "variant"
        )

        if not cart_items.exists():
            messages.error(request, "Your cart is empty.")
            return redirect("cart_page")

        for item in cart_items:
            if item.variant.stock < item.quantity:
                messages.error(request, f"Not enough stock for {item.product.name}.")
                return redirect("cart_page")

            price = item.variant.discounted_price
            original_price = item.variant.price
            item_total = price * item.quantity
            item_discount = (original_price - price) * item.quantity

            order_items_data.append(
                {
                    "product": item.product,
                    "variant": item.variant,
                    "quantity": item.quantity,
                    "price": price,
                    "original_price": original_price,
                    "subtotal": item_total,
                }
            )

            subtotal += item_total
            total_discount += item_discount

    applied_coupon_session = request.session.get("applied_coupon")
    coupon = None
    coupon_discount = Decimal("0.00")

    if applied_coupon_session:
        coupon, coupon_discount, error = validate_coupon(
            applied_coupon_session["code"], request.user, subtotal
        )
        if error:
            request.session.pop("applied_coupon", None)
            request.session.modified = True
            coupon = None
            coupon_discount = Decimal("0.00")

    totals = calculate_checkout_totals(subtotal, coupon_discount)

    delivery_fee = totals["delivery_fee"]
    gst_amount = totals["gst_amount"]
    gst_rate = totals["gst_rate"]
    total_payable = totals["total_payable"]

    if payment_method == "WALLET":
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        if wallet.balance < total_payable:
            messages.error(
                request,
                "Insufficient wallet balance. Please choose another payment method.",
            )
            return redirect("payment_method")

    if payment_method == "RAZORPAY":
        amount_in_paise = int((total_payable * 100).quantize(Decimal("1")))

        client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )

        try:
            razorpay_order = client.order.create(
                {
                    "amount": amount_in_paise,
                    "currency": "INR",
                    "payment_capture": 1,
                    "notes": {
                        "purpose": "ORDER_PAYMENT",
                        "user_id": str(request.user.id),
                    },
                }
            )
        except Exception:
            messages.error(request, "Unable to start payment. Please try again.")
            return redirect("payment_method")

        with transaction.atomic():
            order = Order.objects.create(
                user=request.user,
                address=address,
                payment_method="RAZORPAY",
                payment_status="PENDING",
                order_status="PENDING",
                subtotal=subtotal,
                coupon=coupon,
                discount_amount=total_discount + coupon_discount,
                tax_amount=gst_amount,
                delivery_fee=delivery_fee,
                total_amount=total_payable,
            )

            order.display_id = f"ORD-{order.pk:06d}"
            order.save(update_fields=["display_id"])

            for item in order_items_data:
                OrderItem.objects.create(
                    order=order,
                    product=item["product"],
                    variant=item["variant"],
                    product_name=item["product"].name,
                    size=item["variant"].size,
                    color=item["variant"].color,
                    quantity=item["quantity"],
                    price=item["price"],
                    original_price=item["original_price"],
                    subtotal=item["subtotal"],
                )
                # Stock is NOT deducted here — only once payment is confirmed.

            Payment.objects.create(
                order=order,
                payment_method="RAZORPAY",
                payment_status="PENDING",
                amount=total_payable,
            )

            if coupon:
                # Lock the coupon in now so it can't be reused while this
                # payment is pending.
                locked_coupon = Coupon.objects.select_for_update().get(id=coupon.id)

                if (
                    locked_coupon.usage_limit_global is not None
                    and locked_coupon.times_used >= locked_coupon.usage_limit_global
                ):
                    transaction.set_rollback(True)
                    messages.error(request, "This coupon has reached its usage limit.")
                    return redirect("checkout_page")

                already_used = CouponUsage.objects.filter(
                    coupon=locked_coupon, user=request.user
                ).count()

                if already_used >= locked_coupon.usage_limit_per_user:
                    transaction.set_rollback(True)
                    messages.error(request, "You have already used this coupon.")
                    return redirect("checkout_page")

                CouponUsage.objects.create(
                    coupon=locked_coupon,
                    user=request.user,
                    order=order,
                    discount_amount=coupon_discount,
                )
                locked_coupon.times_used += 1
                locked_coupon.save(update_fields=["times_used"])

                request.session.pop("applied_coupon", None)
                request.session.modified = True

            OrderStatusHistory.objects.create(
                order=order,
                status="PENDING",
                note="Order created, awaiting Razorpay payment.",
            )

        # Session now only needs to remember which order this Razorpay order maps to.
        request.session["pending_razorpay_order"] = {
            "razorpay_order_id": razorpay_order["id"],
            "order_id": str(order.order_id),
        }
        request.session.modified = True

        wallet_for_display, _ = Wallet.objects.get_or_create(user=request.user)

        cart_items_display = []
        for item in order_items_data:
            first_image = item["variant"].images.first()
            cart_items_display.append(
                SimpleNamespace(
                    product=item["product"],
                    variant=item["variant"],
                    quantity=item["quantity"],
                    thumbnail=first_image.image.url if first_image else None,
                    unit_price_amount=item["price"],
                    subtotal_amount=item["subtotal"],
                )
            )

        return render(
            request,
            "checkout/payment_method.html",
            {
                "open_razorpay": True,
                "razorpay_key_id": settings.RAZORPAY_KEY_ID,
                "razorpay_order_id": razorpay_order["id"],
                "amount_in_paise": amount_in_paise,
                "selected_address": address,
                "subtotal": subtotal,
                "gst_amount": gst_amount,
                "gst_rate": gst_rate,
                "delivery_fee": delivery_fee,
                "total_payable": total_payable,
                "wallet_balance": wallet_for_display.balance,
                "checkout_source": source,
                "cart_items": cart_items_display,
            },
        )

    # ---- COD / WALLET: unchanged, create order immediately. ----
    with transaction.atomic():
        order = Order.objects.create(
            user=request.user,
            address=address,
            payment_method=payment_method,
            payment_status="PAID" if payment_method == "WALLET" else "PENDING",
            order_status="PLACED",
            subtotal=subtotal,
            coupon=coupon,
            discount_amount=total_discount + coupon_discount,
            tax_amount=gst_amount,
            delivery_fee=delivery_fee,
            total_amount=total_payable,
        )

        if coupon:
            locked_coupon = Coupon.objects.select_for_update().get(id=coupon.id)

            # Check global usage limit again after locking
            if (
                locked_coupon.usage_limit_global is not None
                and locked_coupon.times_used >= locked_coupon.usage_limit_global
            ):
                transaction.set_rollback(True)
                messages.error(request, "This coupon has reached its usage limit.")
                return redirect("checkout_page")

            # Check per-user usage again
            already_used = CouponUsage.objects.filter(
                coupon=locked_coupon, user=request.user
            ).count()

            if already_used >= locked_coupon.usage_limit_per_user:
                transaction.set_rollback(True)
                messages.error(request, "You have already used this coupon.")
                return redirect("checkout_page")

            CouponUsage.objects.create(
                coupon=locked_coupon,
                user=request.user,
                order=order,
                discount_amount=coupon_discount,
            )

            locked_coupon.times_used += 1
            locked_coupon.save(update_fields=["times_used"])

            request.session.pop("applied_coupon", None)
            request.session.modified = True

        order.display_id = f"ORD-{order.pk:06d}"
        order.save(update_fields=["display_id"])

        for item in order_items_data:
            OrderItem.objects.create(
                order=order,
                product=item["product"],
                variant=item["variant"],
                product_name=item["product"].name,
                size=item["variant"].size,
                color=item["variant"].color,
                quantity=item["quantity"],
                price=item["price"],
                original_price=item["original_price"],
                subtotal=item["subtotal"],
            )

            item["variant"].stock -= item["quantity"]
            item["variant"].save()

        payment = Payment.objects.create(
            order=order,
            payment_method=payment_method,
            payment_status="PAID" if payment_method == "WALLET" else "PENDING",
            amount=total_payable,
        )

        status_note = "Order placed successfully."

        if payment_method == "WALLET":
            wallet = Wallet.objects.select_for_update().get(user=request.user)

            if wallet.balance < total_payable:
                transaction.set_rollback(True)
                messages.error(
                    request,
                    "Insufficient wallet balance. Please choose another payment method.",
                )
                return redirect("payment_method")

            wallet.balance -= total_payable
            wallet.save(update_fields=["balance", "updated_at"])

            WalletTransaction.objects.create(
                wallet=wallet,
                amount=total_payable,
                transaction_type="DEBIT",
                purpose="PURCHASE",
                order_id=str(order.order_id),
                description=f"Payment for order {order.display_id}.",
            )

            payment.transaction_id = f"WALLET-{order.display_id}"
            payment.save(update_fields=["transaction_id"])

            status_note = "Order placed and paid via wallet."

        OrderStatusHistory.objects.create(
            order=order, status="PLACED", note=status_note
        )
        apply_referral_reward(order)

    if source == "buy_now":
        request.session.pop("buy_now", None)
        request.session.modified = True
    else:
        Cart.objects.filter(user=request.user).delete()

    messages.success(request, "Order placed successfully.")
    return redirect("order_success", order_id=order.order_id)


@login_required(login_url="user_login")
def order_success(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("user", "address", "coupon").prefetch_related(
            "items",
            "items__product",
            "items__variant",
            "items__variant__images",
            "coupon_usage",
        ),
        order_id=order_id,
        user=request.user,
    )

    total_savings = sum(
        (item.original_price - item.price) * item.quantity
        for item in order.items.all()
        if item.original_price is not None and item.original_price > item.price
    )

    coupon_discount = Decimal("0.00")
    if order.coupon:
        usage = order.coupon_usage.first()
        if usage:
            coupon_discount = usage.discount_amount

    return render(
        request,
        "orders/order_success.html",
        {
            "order": order,
            "total_savings": total_savings,
            "coupon_discount": coupon_discount,
        },
    )


@login_required(login_url="admin_login")
def admin_order_list(request):
    orders_queryset = Order.objects.select_related("user", "address").order_by(
        "-created_at"
    )

    # -----------------------------
    # GET values
    # -----------------------------
    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    sort_by = request.GET.get("sort", "newest").strip()
    min_amount = request.GET.get("min_amount", "").strip()
    max_amount = request.GET.get("max_amount", "").strip()

    # -----------------------------
    # Search
    # -----------------------------
    if query:
        orders_queryset = orders_queryset.filter(
            Q(display_id__icontains=query)
            | Q(user__fullname__icontains=query)
            | Q(user__email__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
        )

    # -----------------------------
    # Status filter
    # -----------------------------
    if status_filter:
        orders_queryset = orders_queryset.filter(order_status=status_filter)

    # -----------------------------
    # Amount filter
    # -----------------------------
    if min_amount:
        try:
            min_amount_decimal = Decimal(min_amount)
            orders_queryset = orders_queryset.filter(
                total_amount__gte=min_amount_decimal
            )
        except InvalidOperation:
            messages.error(request, "Invalid minimum amount.")

    if max_amount:
        try:
            max_amount_decimal = Decimal(max_amount)
            orders_queryset = orders_queryset.filter(
                total_amount__lte=max_amount_decimal
            )
        except InvalidOperation:
            messages.error(request, "Invalid maximum amount.")

    # -----------------------------
    # Sorting
    # -----------------------------
    if sort_by == "oldest":
        orders_queryset = orders_queryset.order_by("created_at")
    elif sort_by == "amount_high":
        orders_queryset = orders_queryset.order_by("-total_amount")
    elif sort_by == "amount_low":
        orders_queryset = orders_queryset.order_by("total_amount")
    else:
        orders_queryset = orders_queryset.order_by("-created_at")

    # -----------------------------
    # Stats cards
    # Use all orders, not filtered orders
    # -----------------------------
    total_orders = Order.objects.count()
    pending_orders = Order.objects.filter(order_status="PENDING").count()
    delivered_orders = Order.objects.filter(order_status="DELIVERED").count()
    cancelled_orders = Order.objects.filter(order_status="CANCELLED").count()

    # -----------------------------
    # Pagination
    # -----------------------------
    paginator = Paginator(orders_queryset, 10)
    page_number = request.GET.get("page")
    orders = paginator.get_page(page_number)

    for order in orders:
        billable_items = order.items.filter(item_status__in=BILLABLE_ITEM_STATUSES)

        cancelled_items = order.items.filter(item_status="CANCELLED")
        returned_items = order.items.filter(item_status="RETURNED")

        order.billable_subtotal = sum(item.subtotal for item in billable_items)
        order.cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
        order.returned_subtotal = sum(item.subtotal for item in returned_items)

        order.cancelled_items_count = cancelled_items.count()
        order.returned_items_count = returned_items.count()

        coupon_discount = Decimal("0.00")
        if order.coupon:
            coupon_discount = sum(
                get_item_refund_amount(order, item)[2] for item in billable_items
            )

        if order.billable_subtotal > 0:
            totals = calculate_checkout_totals(order.billable_subtotal, coupon_discount)
            order.display_delivery_fee = totals["delivery_fee"]
            order.display_gst_amount = totals["gst_amount"]
            order.display_gst_rate = totals["gst_rate"]
            order.display_total_amount = totals["total_payable"]
        else:
            order.display_delivery_fee = Decimal("0.00")
            order.display_gst_amount = Decimal("0.00")
            order.display_total_amount = Decimal("0.00")

    context = {
        "orders": orders,
        "query": query,
        "status_filter": status_filter,
        "sort_by": sort_by,
        "min_amount": min_amount,
        "max_amount": max_amount,
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "delivered_orders": delivered_orders,
        "cancelled_orders": cancelled_orders,
    }

    return render(request, "orders/order_list.html", context)


@login_required(login_url="admin_login")
def admin_order_detail(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("user", "address", "payment").prefetch_related(
            "items",
            "items__product",
            "items__variant",
            "items__variant__images",
            "status_history",
        ),
        order_id=order_id,
    )

    try:
        payment = order.payment
    except Payment.DoesNotExist:
        payment = None

    status_history = order.status_history.all().order_by("created_at")

    billable_items = order.items.filter(item_status__in=BILLABLE_ITEM_STATUSES)

    cancelled_items = order.items.filter(item_status="CANCELLED")
    returned_items = order.items.filter(item_status="RETURNED")

    billable_subtotal = sum(item.subtotal for item in billable_items)
    cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
    returned_subtotal = sum(item.subtotal for item in returned_items)

    coupon_discount = Decimal("0.00")
    if order.coupon:
        coupon_discount = sum(
            get_item_refund_amount(order, item)[2] for item in billable_items
        )

    if billable_subtotal > 0:
        totals = calculate_checkout_totals(billable_subtotal, coupon_discount)
        display_delivery_fee = totals["delivery_fee"]
        display_gst_amount = totals["gst_amount"]
        display_gst_rate = totals["gst_rate"]
        display_total_amount = totals["total_payable"]
    else:
        display_delivery_fee = Decimal("0.00")
        display_gst_amount = Decimal("0.00")
        display_gst_rate = Decimal("0.00")
        display_total_amount = Decimal("0.00")

    return render(
        request,
        "orders/order_detail.html",
        {
            "order": order,
            "payment": payment,
            "status_history": status_history,
            "billable_subtotal": billable_subtotal,
            "cancelled_subtotal": cancelled_subtotal,
            "returned_subtotal": returned_subtotal,
            "coupon_discount": coupon_discount,
            "display_delivery_fee": display_delivery_fee,
            "display_gst_amount": display_gst_amount,
            "display_gst_rate": display_gst_rate,
            "display_total_amount": display_total_amount,
        },
    )


@login_required(login_url="admin_login")
def admin_change_order_status(request, order_id):
    order = get_object_or_404(Order, order_id=order_id)

    if request.method == "POST":
        new_status = request.POST.get("order_status")
        note = request.POST.get("note", "").strip()

        valid_statuses = [choice[0] for choice in Order.ORDER_STATUS_CHOICES]

        if new_status not in valid_statuses:
            messages.error(request, "Invalid order status.")
            return redirect("admin_change_order_status", order_id=order.order_id)

        if order.order_status in ["DELIVERED", "CANCELLED"]:
            messages.error(request, "Delivered or cancelled orders cannot be changed.")
            return redirect("admin_order_detail", order_id=order.order_id)

        if order.order_status == new_status:
            messages.info(request, "Order status is already the same.")
            return redirect("admin_change_order_status", order_id=order.order_id)

        if new_status == "DELIVERED":
            order.payment_status = "PAID"
            order.save(update_fields=["payment_status"])

        old_status = order.order_status
        order.order_status = new_status
        order.save()

        OrderStatusHistory.objects.create(
            order=order,
            status=new_status,
            note=note or f"Status changed from {old_status} to {new_status}.",
        )

        messages.success(request, "Order status updated successfully.")
        return redirect("admin_order_detail", order_id=order.order_id)

    return render(
        request,
        "orders/change_order_status.html",
        {
            "order": order,
            "status_choices": Order.ORDER_STATUS_CHOICES,
        },
    )


@login_required(login_url="admin_login")
def inventory_list(request):
    LOW_STOCK_LIMIT = 5

    query = request.GET.get("q", "").strip()
    stock_filter = request.GET.get("stock", "").strip()
    category_filter = request.GET.get("category", "").strip()

    products_queryset = (
        Product.objects.select_related("subcategory", "subcategory__category")
        .prefetch_related(
            Prefetch(
                "variants", queryset=ProductVariant.objects.prefetch_related("images")
            )
        )
        .annotate(
            total_stock=Coalesce(Sum("variants__stock"), Value(0)),
            variant_count=Count("variants", distinct=True),
        )
        .order_by("-id")
    )

    # Search by product name or product id
    if query:
        products_queryset = products_queryset.filter(
            Q(name__icontains=query) | Q(id__icontains=query)
        )

    # Filter by category
    if category_filter:
        products_queryset = products_queryset.filter(
            subcategory__category_id=category_filter
        )

    # Filter by stock level
    if stock_filter == "out_of_stock":
        products_queryset = products_queryset.filter(total_stock__lte=0)
    elif stock_filter == "low_stock":
        products_queryset = products_queryset.filter(
            total_stock__gt=0, total_stock__lte=LOW_STOCK_LIMIT
        )
    elif stock_filter == "in_stock":
        products_queryset = products_queryset.filter(total_stock__gt=LOW_STOCK_LIMIT)

    # Stats cards should count all products, not only filtered products
    stats_queryset = Product.objects.annotate(
        total_stock=Coalesce(Sum("variants__stock"), Value(0))
    )

    total_products = stats_queryset.aggregate(
        total=Coalesce(Sum("total_stock"), Value(0))
    )["total"]

    out_of_stock_count = ProductVariant.objects.filter(stock__lte=0).count()

    low_stock_count = ProductVariant.objects.filter(
        stock__gt=0, stock__lte=LOW_STOCK_LIMIT
    ).count()

    paginator = Paginator(products_queryset, 8)
    page_number = request.GET.get("page")
    products = paginator.get_page(page_number)

    # Add thumbnail manually
    for product in products:
        product.thumbnail = None

        first_variant = product.variants.first()

        if first_variant:
            first_image = first_variant.images.first()
            if first_image:
                product.thumbnail = first_image.image.url

        out_of_stock_variants = sum(1 for v in product.variants.all() if v.stock == 0)
        low_stock_variants = sum(
            1 for v in product.variants.all() if 0 < v.stock <= LOW_STOCK_LIMIT
        )

        product.out_of_stock_variants = out_of_stock_variants
        product.low_stock_variants = low_stock_variants

    categories = Category.objects.all().order_by("name")

    context = {
        "products": products,
        "categories": categories,
        "query": query,
        "stock_filter": stock_filter,
        "category_filter": category_filter,
        "total_products": total_products,
        "out_of_stock_count": out_of_stock_count,
        "low_stock_count": low_stock_count,
        "low_stock_limit": LOW_STOCK_LIMIT,
    }

    return render(request, "inventory/inventory_list.html", context)


@login_required(login_url="admin_login")
def update_inventory_stock(request, product_id):
    if request.method != "POST":
        return redirect("inventory_list")

    product = get_object_or_404(Product, id=product_id)

    variants = ProductVariant.objects.filter(product=product)

    for variant in variants:
        stock_value = request.POST.get(f"stock_{variant.id}")

        if stock_value is None:
            continue

        try:
            stock = int(stock_value)
        except ValueError:
            messages.error(request, "Invalid stock value.")
            return redirect("inventory_list")

        if stock < 0:
            messages.error(request, "Stock cannot be negative.")
            return redirect("inventory_list")

        variant.stock = stock
        variant.save(update_fields=["stock"])

    messages.success(request, "Stock updated successfully.")
    return redirect("inventory_list")


@login_required(login_url="user_login")
def user_orders(request):
    query = request.GET.get("q", "").strip()

    orders = (
        Order.objects.filter(user=request.user)
        .prefetch_related("items", "items__variant")
        .order_by("-created_at")
    )

    if query:
        orders = orders.filter(
            Q(items__product_name__icontains=query) | Q(order_status__icontains=query)
        ).distinct()

    for order in orders:
        billable_items = order.items.filter(item_status__in=BILLABLE_ITEM_STATUSES)

        active_items = order.items.filter(item_status="ACTIVE")
        cancelled_items = order.items.filter(item_status="CANCELLED")
        returned_items = order.items.filter(item_status="RETURNED")

        order.has_active_items = active_items.exists()

        order.billable_subtotal = sum(item.subtotal for item in billable_items)
        order.cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
        order.returned_subtotal = sum(item.subtotal for item in returned_items)

        order.cancelled_items_count = cancelled_items.count()
        order.returned_items_count = returned_items.count()

        if order.items.filter(item_status="RETURN_REQUESTED").exists():
            order.return_status_summary = "RETURN_REQUESTED"
        elif order.items.filter(item_status="RETURN_APPROVED").exists():
            order.return_status_summary = "RETURN_APPROVED"
        elif order.items.filter(item_status="RETURNED").exists():
            order.return_status_summary = "RETURNED"
        else:
            order.return_status_summary = None

        # Compute coupon discount so total matches detail page.
        order.coupon_discount = Decimal("0.00")
        if order.coupon:
            order.coupon_discount = sum(
                get_item_refund_amount(order, item)[2] for item in billable_items
            )

        if order.billable_subtotal > 0:
            totals = calculate_checkout_totals(
                order.billable_subtotal, order.coupon_discount
            )
            order.display_delivery_fee = totals["delivery_fee"]
            order.display_gst_amount = totals["gst_amount"]
            order.display_gst_rate = totals["gst_rate"]
            order.display_total_amount = totals["total_payable"]
        else:
            order.display_delivery_fee = Decimal("0.00")
            order.display_gst_amount = Decimal("0.00")
            order.display_total_amount = Decimal("0.00")

    paginator = Paginator(orders, 10)
    page_number = request.GET.get("page")
    orders = paginator.get_page(page_number)

    return render(
        request,
        "orders/user_orders.html",
        {
            "orders": orders,
            "query": query,
        },
    )


@login_required(login_url="user_login")
def user_order_detail(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("user", "address").prefetch_related(
            "items",
            "items__product",
            "items__variant",
            "items__variant__images",
            "status_history",
        ),
        order_id=order_id,
        user=request.user,
    )

    status_history = order.status_history.all().order_by("created_at")

    is_pending_payment = (
        order.payment_method == "RAZORPAY" and order.payment_status == "PENDING"
    )

    can_cancel = order.order_status in ["PENDING", "PLACED"]

    can_request_return = (
        order.order_status == "DELIVERED"
        and order.items.filter(item_status="ACTIVE").exists()
    )

    cancelled_item_ids = request.session.pop(
        f"cancel_success_items_{order.order_id}", []
    )

    cancelled_success_items = OrderItem.objects.filter(
        id__in=cancelled_item_ids, order=order, item_status="CANCELLED"
    )

    return_item_ids = request.session.pop(
        f"return_success_items_{order.order_id}", []
    )

    return_success_items = OrderItem.objects.filter(
        id__in=return_item_ids, order=order, item_status="RETURN_REQUESTED"
    )

    show_return_success_modal = return_success_items.exists()

    show_cancel_success_modal = cancelled_success_items.exists()

    billable_items = order.items.filter(item_status__in=BILLABLE_ITEM_STATUSES)

    cancelled_items = order.items.filter(item_status="CANCELLED")
    returned_items = order.items.filter(item_status="RETURNED")

    active_subtotal = sum(item.subtotal for item in billable_items)
    active_discount = sum(
        (item.original_price - item.price) * item.quantity
        for item in billable_items
        if item.original_price is not None
    )
    cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
    returned_subtotal = sum(item.subtotal for item in returned_items)

    coupon_discount = Decimal("0.00")
    if order.coupon:
        coupon_discount = sum(
            get_item_refund_amount(order, item)[2] for item in billable_items
        )

    if active_subtotal > 0:
        totals = calculate_checkout_totals(active_subtotal, coupon_discount)
        display_delivery_fee = totals["delivery_fee"]
        display_gst_amount = totals["gst_amount"]
        display_gst_rate = totals["gst_rate"]
        display_total_amount = totals["total_payable"]
    else:
        display_delivery_fee = Decimal("0.00")
        display_gst_amount = Decimal("0.00")
        display_gst_rate = Decimal("0.00")
        display_total_amount = Decimal("0.00")

    return_request_by_item = {}
    for ret in order.return_requests.all():
        return_request_by_item[ret.order_item_id] = ret

    for item in order.items.all():
        item.return_request = return_request_by_item.get(item.id)

    return render(
        request,
        "orders/user_order_detail.html",
        {
            "order": order,
            "status_history": status_history,
            "can_cancel": can_cancel,
            "can_request_return": can_request_return,
            "cancelled_success_items": cancelled_success_items,
            "show_cancel_success_modal": show_cancel_success_modal,
            "active_subtotal": active_subtotal,
            "cancelled_subtotal": cancelled_subtotal,
            "display_delivery_fee": display_delivery_fee,
            "display_gst_amount": display_gst_amount,
            "display_gst_rate": display_gst_rate,
            "display_total_amount": display_total_amount,
            "return_success_items": return_success_items,
            "show_return_success_modal": show_return_success_modal,
            "returned_subtotal": returned_subtotal,
            "active_discount": active_discount,
            "coupon_discount": coupon_discount,
            "is_pending_payment": is_pending_payment,
        },
    )


@login_required(login_url="user_login")
def user_cancel_order_select(request, order_id):
    if request.method != "POST":
        return redirect("user_order_detail", order_id=order_id)

    order = get_object_or_404(Order, order_id=order_id, user=request.user)

    cancellable_statuses = ["PENDING", "PLACED"]

    if order.order_status not in cancellable_statuses:
        messages.error(request, "This order cannot be cancelled now.")
        return redirect("user_order_detail", order_id=order.order_id)

    selected_items = request.POST.getlist("selected_items")

    if not selected_items:
        messages.error(request, "Please select at least one item to cancel.")
        return redirect("user_order_detail", order_id=order.order_id)

    valid_items = OrderItem.objects.filter(
        id__in=selected_items, order=order, item_status="ACTIVE"
    )

    if not valid_items.exists():
        messages.error(request, "No valid items selected.")
        return redirect("user_order_detail", order_id=order.order_id)

    request.session[f"cancel_items_{order.order_id}"] = [
        str(item.id) for item in valid_items
    ]
    request.session.modified = True

    return redirect("user_cancel_order_page", order_id=order.order_id)


@login_required(login_url="user_login")
def user_cancel_order_page(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("address").prefetch_related(
            "items", "items__variant", "items__variant__images"
        ),
        order_id=order_id,
        user=request.user,
    )

    selected_item_ids = request.session.get(f"cancel_items_{order.order_id}", [])

    selected_items = OrderItem.objects.filter(
        id__in=selected_item_ids, order=order, item_status="ACTIVE"
    )

    if not selected_items.exists():
        messages.error(request, "Please select items to cancel.")
        return redirect("user_order_detail", order_id=order.order_id)

    estimated_refund = sum(
        get_item_refund_amount(order, item)[0] for item in selected_items
    )

    return render(
        request,
        "orders/cancel_order.html",
        {
            "order": order,
            "selected_items": selected_items,
            "estimated_refund": estimated_refund,
            "cancellation_fee": 0,
            "cancellation_reasons": CANCELLATION_REASONS,
        },
    )


@login_required(login_url="user_login")
def user_confirm_cancel_items(request, order_id):
    if request.method != "POST":
        return redirect("user_cancel_order_page", order_id=order_id)

    order = get_object_or_404(Order, order_id=order_id, user=request.user)

    selected_item_ids = request.session.get(f"cancel_items_{order.order_id}", [])

    selected_items = OrderItem.objects.filter(
        id__in=selected_item_ids, order=order, item_status="ACTIVE"
    )

    if not selected_items.exists():
        messages.error(request, "No valid items selected.")
        return redirect("user_order_detail", order_id=order.order_id)

    reason = request.POST.get("reason", "").strip()
    comment = request.POST.get("comment", "").strip()

    valid_reason_keys = [key for key, label in CANCELLATION_REASONS]

    if reason not in valid_reason_keys:
        messages.error(request, "Please select a valid cancellation reason.")
        return redirect("user_cancel_order_page", order_id=order.order_id)

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get_or_create(user=request.user)[0]
        total_refund = Decimal("0.00")

        for item in selected_items:
            item.item_status = "CANCELLED"
            item.cancel_reason = reason
            item.cancel_comment = comment
            item.cancelled_at = timezone.now()
            item.save(
                update_fields=[
                    "item_status",
                    "cancel_reason",
                    "cancel_comment",
                    "cancelled_at",
                ]
            )

            if item.variant:
                item.variant.stock += item.quantity
                item.variant.save(update_fields=["stock"])

            refund_amount, _, _ = get_item_refund_amount(order, item)
            total_refund += refund_amount

        if total_refund > 0 and order.payment_status == "PAID":
            wallet.balance += total_refund
            wallet.save(update_fields=["balance", "updated_at"])

            WalletTransaction.objects.create(
                wallet=wallet,
                amount=total_refund,
                transaction_type="CREDIT",
                purpose="REFUND",
                order_id=str(order.order_id),
                description=(
                    f"Refund for {selected_items.count()} cancelled item(s) "
                    f"in order {order.display_id}."
                ),
            )

        active_items_left = order.items.filter(item_status="ACTIVE").exists()

        if not active_items_left:
            order.order_status = "CANCELLED"
            order.save(update_fields=["order_status"])

            OrderStatusHistory.objects.create(
                order=order,
                status="CANCELLED",
                note="Order cancelled by customer. Amount refunded to wallet.",
            )
        else:
            OrderStatusHistory.objects.create(
                order=order,
                status=order.order_status,
                note="Some items were cancelled by customer. Amount refunded to wallet.",
            )

    cancelled_item_ids_for_success = [str(item.id) for item in selected_items]

    request.session.pop(f"cancel_items_{order.order_id}", None)

    request.session[f"cancel_success_items_{order.order_id}"] = (
        cancelled_item_ids_for_success
    )
    request.session.modified = True

    return redirect("user_order_detail", order_id=order.order_id)


@login_required(login_url="user_login")
def user_return_order_select(request, order_id):
    if request.method != "POST":
        return redirect("user_order_detail", order_id=order_id)

    order = get_object_or_404(Order, order_id=order_id, user=request.user)

    if order.order_status != "DELIVERED":
        messages.error(request, "Return is available only after delivery.")
        return redirect("user_order_detail", order_id=order.order_id)

    selected_items = request.POST.getlist("selected_items")

    if not selected_items:
        messages.error(request, "Please select at least one item to return.")
        return redirect("user_order_detail", order_id=order.order_id)

    valid_items = OrderItem.objects.filter(
        id__in=selected_items, order=order, item_status="ACTIVE"
    )

    if not valid_items.exists():
        messages.error(request, "No valid items selected for return.")
        return redirect("user_order_detail", order_id=order.order_id)

    request.session[f"return_items_{order.order_id}"] = [
        str(item.id) for item in valid_items
    ]
    request.session.modified = True

    return redirect("user_return_order_page", order_id=order.order_id)


@login_required(login_url="user_login")
def user_return_order_page(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("address").prefetch_related(
            "items", "items__variant", "items__variant__images"
        ),
        order_id=order_id,
        user=request.user,
    )

    if order.order_status != "DELIVERED":
        messages.error(request, "Return is available only after delivery.")
        return redirect("user_order_detail", order_id=order.order_id)

    selected_item_ids = request.session.get(f"return_items_{order.order_id}", [])

    selected_items = OrderItem.objects.filter(
        id__in=selected_item_ids, order=order, item_status="ACTIVE"
    )

    if not selected_items.exists():
        messages.error(request, "Please select items to return.")
        return redirect("user_order_detail", order_id=order.order_id)

    estimated_refund = sum(
        get_item_refund_amount(order, item)[0] for item in selected_items
    )

    return render(
        request,
        "returns/return_order.html",
        {
            "order": order,
            "selected_items": selected_items,
            "estimated_refund": estimated_refund,
            "restocking_fee": 0,
            "return_reasons": RETURN_REASONS,
        },
    )


@login_required(login_url="user_login")
def user_confirm_return_items(request, order_id):
    if request.method != "POST":
        return redirect("user_return_order_page", order_id=order_id)

    order = get_object_or_404(Order, order_id=order_id, user=request.user)

    if order.order_status != "DELIVERED":
        messages.error(request, "Return is available only after delivery.")
        return redirect("user_order_detail", order_id=order.order_id)

    selected_item_ids = request.session.get(f"return_items_{order.order_id}", [])

    selected_items = OrderItem.objects.filter(
        id__in=selected_item_ids, order=order, item_status="ACTIVE"
    )

    if not selected_items.exists():
        messages.error(request, "No valid items selected.")
        return redirect("user_order_detail", order_id=order.order_id)

    reason = request.POST.get("reason", "").strip()
    comment = request.POST.get("comment", "").strip()

    valid_reason_keys = [key for key, label in RETURN_REASONS]

    if reason not in valid_reason_keys:
        messages.error(request, "Please select a valid return reason.")
        return redirect("user_return_order_page", order_id=order.order_id)

    with transaction.atomic():
        batch_id = uuid.uuid4()

        for item in selected_items:
            item.item_status = "RETURN_REQUESTED"
            item.return_reason = reason
            item.return_comment = comment
            item.return_requested_at = timezone.now()
            item.save(
                update_fields=[
                    "item_status",
                    "return_reason",
                    "return_comment",
                    "return_requested_at",
                ]
            )

            refund_amount, _, _ = get_item_refund_amount(order, item)

            ReturnRequest.objects.create(
                order=order,
                order_item=item,
                user=request.user,
                reason=reason,
                comment=comment,
                refund_amount=refund_amount,
                status="REQUESTED",
                batch_id=batch_id,
            )

        OrderStatusHistory.objects.create(
            order=order,
            status=order.order_status,
            note="Return request submitted.",
        )

    return_item_ids_for_success = [str(item.id) for item in selected_items]

    request.session.pop(f"return_items_{order.order_id}", None)
    request.session[f"return_success_items_{order.order_id}"] = (
        return_item_ids_for_success
    )
    request.session.modified = True

    return redirect("user_order_detail", order_id=order.order_id)


@login_required(login_url="user_login")
def download_invoice(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("user", "address").prefetch_related(
            "items", "items__product", "items__variant"
        ),
        order_id=order_id,
        user=request.user,
    )

    billable_items = order.items.filter(item_status__in=BILLABLE_ITEM_STATUSES)

    cancelled_items = order.items.filter(item_status="CANCELLED")
    returned_items = order.items.filter(item_status="RETURNED")

    billable_subtotal = sum(item.subtotal for item in billable_items)
    cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
    returned_subtotal = sum(item.subtotal for item in returned_items)

    # Coupon discount attributable only to items still counted as billable —
    # keeps the invoice total consistent with cancellations/returns.
    coupon_discount = Decimal("0.00")
    if order.coupon:
        coupon_discount = sum(
            get_item_refund_amount(order, item)[2] for item in billable_items
        )

    if billable_subtotal > 0:
        totals = calculate_checkout_totals(billable_subtotal, coupon_discount)
        display_delivery_fee = totals["delivery_fee"]
        display_gst_amount = totals["gst_amount"]
        display_gst_rate = totals["gst_rate"]
        display_total_amount = totals["total_payable"]
    else:
        display_delivery_fee = Decimal("0.00")
        display_gst_amount = Decimal("0.00")
        display_gst_rate = Decimal("0.00")
        display_total_amount = Decimal("0.00")

    html_string = render_to_string(
        "orders/invoice.html",
        {
            "order": order,
            "billable_subtotal": billable_subtotal,
            "cancelled_subtotal": cancelled_subtotal,
            "returned_subtotal": returned_subtotal,
            "display_delivery_fee": display_delivery_fee,
            "display_gst_amount": display_gst_amount,
            "display_gst_rate": display_gst_rate,
            "display_total_amount": display_total_amount,
            "coupon_discount": coupon_discount,
        },
    )

    pdf_file = HTML(
        string=html_string, base_url=request.build_absolute_uri("/")
    ).write_pdf()

    response = HttpResponse(pdf_file, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="invoice_{order.order_id}.pdf"'

    return response


@login_required(login_url="admin_login")
def admin_return_list(request):
    returns_queryset = (
        ReturnRequest.objects.select_related(
            "user",
            "order",
            "order_item",
            "order_item__product",
            "order_item__variant",
        )
        .annotate(
            order_total_returns=Count("order__return_requests"),
            order_approved=Count(
                "order__return_requests",
                filter=Q(order__return_requests__status="APPROVED"),
            ),
            order_rejected=Count(
                "order__return_requests",
                filter=Q(order__return_requests__status="REJECTED"),
            ),
            order_refunded=Count(
                "order__return_requests",
                filter=Q(order__return_requests__status="REFUNDED"),
            ),
        )
        .order_by("-requested_at")
    )

    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    sort_by = request.GET.get("sort", "newest").strip()
    min_amount = request.GET.get("min_amount", "").strip()
    max_amount = request.GET.get("max_amount", "").strip()

    if query:
        returns_queryset = returns_queryset.filter(
            Q(order__display_id__icontains=query)
            | Q(order_item__product_name__icontains=query)
            | Q(user__fullname__icontains=query)
            | Q(user__email__icontains=query)
        )

    if status_filter:
        returns_queryset = returns_queryset.filter(status=status_filter)

    if min_amount:
        try:
            returns_queryset = returns_queryset.filter(
                refund_amount__gte=Decimal(min_amount)
            )
        except InvalidOperation:
            messages.error(request, "Invalid minimum refund amount.")

    if max_amount:
        try:
            returns_queryset = returns_queryset.filter(
                refund_amount__lte=Decimal(max_amount)
            )
        except InvalidOperation:
            messages.error(request, "Invalid maximum refund amount.")

    if sort_by == "oldest":
        returns_queryset = returns_queryset.order_by("requested_at")
    elif sort_by == "amount_high":
        returns_queryset = returns_queryset.order_by("-refund_amount")
    elif sort_by == "amount_low":
        returns_queryset = returns_queryset.order_by("refund_amount")
    else:
        returns_queryset = returns_queryset.order_by("-requested_at")

    total_returns = ReturnRequest.objects.count()
    requested_returns = ReturnRequest.objects.filter(status="REQUESTED").count()
    approved_returns = ReturnRequest.objects.filter(status="APPROVED").count()
    refunded_returns = ReturnRequest.objects.filter(status="REFUNDED").count()
    rejected_returns = ReturnRequest.objects.filter(status="REJECTED").count()

    paginator = Paginator(returns_queryset, 10)
    page_number = request.GET.get("page")
    returns = paginator.get_page(page_number)

    context = {
        "returns": returns,
        "query": query,
        "status_filter": status_filter,
        "sort_by": sort_by,
        "min_amount": min_amount,
        "max_amount": max_amount,
        "total_returns": total_returns,
        "requested_returns": requested_returns,
        "approved_returns": approved_returns,
        "refunded_returns": refunded_returns,
        "rejected_returns": rejected_returns,
    }

    return render(request, "returns/return_list.html", context)


@login_required(login_url="admin_login")
def admin_return_detail(request, return_id):
    return_request = get_object_or_404(
        ReturnRequest.objects.select_related(
            "user",
            "order",
            "order_item",
            "order_item__product",
            "order_item__variant",
        ),
        return_id=return_id,
    )

    if return_request.batch_id:
        batch_returns = (
            ReturnRequest.objects.filter(batch_id=return_request.batch_id)
            .select_related(
                "order",
                "order__coupon",
                "order_item",
                "order_item__product",
                "order_item__variant",
            )
            .prefetch_related("order_item__variant__images", "order__coupon_usage")
            .order_by("requested_at")
        )
    else:
        batch_returns = (
            ReturnRequest.objects.filter(pk=return_request.pk)
            .select_related(
                "order",
                "order__coupon",
                "order_item",
                "order_item__product",
                "order_item__variant",
            )
            .prefetch_related("order_item__variant__images", "order__coupon_usage")
        )

    total_refund = Decimal("0.00")

    for ret in batch_returns:
        correct_amount, offer_savings, coupon_share = get_item_refund_amount(
            ret.order, ret.order_item
        )

        # Self-heal stale/incorrect stored values from before the fix.
        if ret.refund_amount != correct_amount:
            ret.refund_amount = correct_amount
            ret.save(update_fields=["refund_amount"])

        ret.offer_savings = offer_savings
        ret.coupon_share = coupon_share

        total_refund += correct_amount

    reason_dict = dict(RETURN_REASONS)

    display_reason = reason_dict.get(return_request.reason, return_request.reason)

    return render(
        request,
        "returns/return_detail.html",
        {
            "return_request": return_request,
            "display_reason": display_reason,
            "total_refund": total_refund,
            "batch_returns": batch_returns,
        },
    )


@login_required(login_url="admin_login")
def admin_approve_returns(request, return_id):
    if request.method != "POST":
        return redirect("admin_return_detail", return_id=return_id)

    return_request = get_object_or_404(ReturnRequest, return_id=return_id)
    selected_ids = request.POST.getlist("selected_returns")

    if not selected_ids:
        messages.error(request, "Please select at least one item to approve.")
        return redirect("admin_return_detail", return_id=return_id)

    returns_to_approve = ReturnRequest.objects.filter(
        return_id__in=selected_ids, order=return_request.order, status="REQUESTED"
    ).select_related("order_item")

    if not returns_to_approve.exists():
        messages.error(request, "No valid return requests selected.")
        return redirect("admin_return_detail", return_id=return_id)

    with transaction.atomic():
        for ret in returns_to_approve:
            ret.status = "APPROVED"
            ret.save(update_fields=["status", "updated_at"])

            ret.order_item.item_status = "RETURN_APPROVED"
            ret.order_item.save(update_fields=["item_status"])

        OrderStatusHistory.objects.create(
            order=return_request.order,
            status=return_request.order.order_status,
            note=f"Return approved for {returns_to_approve.count()} item(s).",
        )

    messages.success(request, "Selected return requests approved.")
    return redirect("admin_return_detail", return_id=return_id)


@login_required(login_url="admin_login")
def admin_mark_refunded(request, return_id):
    if request.method != "POST":
        return redirect("admin_return_detail", return_id=return_id)

    return_request = get_object_or_404(ReturnRequest, return_id=return_id)
    selected_ids = request.POST.getlist("selected_returns")

    if not selected_ids:
        messages.error(
            request, "Please select at least one item to mark as refunded."
        )
        return redirect("admin_return_detail", return_id=return_id)

    returns_to_refund = ReturnRequest.objects.filter(
        return_id__in=selected_ids, order=return_request.order, status="APPROVED"
    ).select_related("order_item", "order")

    if not returns_to_refund.exists():
        messages.error(request, "No valid approved return requests selected.")
        return redirect("admin_return_detail", return_id=return_id)

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get_or_create(
            user=return_request.user
        )[0]
        total_refund = Decimal("0.00")

        for ret in returns_to_refund:
            ret.status = "REFUNDED"
            ret.save(update_fields=["status", "updated_at"])

            ret.order_item.item_status = "RETURNED"
            ret.order_item.save(update_fields=["item_status"])

            if ret.order_item.variant:
                ret.order_item.variant.stock += ret.order_item.quantity
                ret.order_item.variant.save(update_fields=["stock"])

            refund_amount, _, _ = get_item_refund_amount(ret.order, ret.order_item)
            total_refund += refund_amount

        if total_refund > 0:
            wallet.balance += total_refund
            wallet.save(update_fields=["balance", "updated_at"])

            WalletTransaction.objects.create(
                wallet=wallet,
                amount=total_refund,
                transaction_type="CREDIT",
                purpose="REFUND",
                order_id=str(return_request.order.order_id),
                description=(
                    f"Refund for {returns_to_refund.count()} returned item(s) "
                    f"in order {return_request.order.display_id}."
                ),
            )

        OrderStatusHistory.objects.create(
            order=return_request.order,
            status=return_request.order.order_status,
            note=(
                f"Refund processed for {returns_to_refund.count()} item(s). "
                f"₹{total_refund} credited to wallet."
            ),
        )

    messages.success(
        request, f"Marked as refunded. ₹{total_refund} credited to customer wallet."
    )
    return redirect("admin_return_detail", return_id=return_id)


@login_required(login_url="admin_login")
def admin_reject_returns(request, return_id):
    if request.method != "POST":
        return redirect("admin_return_detail", return_id=return_id)

    return_request = get_object_or_404(ReturnRequest, return_id=return_id)
    selected_ids = request.POST.getlist("selected_returns")

    if not selected_ids:
        messages.error(request, "Please select at least one item to reject.")
        return redirect("admin_return_detail", return_id=return_id)

    returns_to_reject = ReturnRequest.objects.filter(
        return_id__in=selected_ids, order=return_request.order, status="REQUESTED"
    ).select_related("order_item")

    if not returns_to_reject.exists():
        messages.error(request, "No valid return requests selected.")
        return redirect("admin_return_detail", return_id=return_id)

    rejection_note = request.POST.get("rejection_note", "").strip()

    with transaction.atomic():
        for ret in returns_to_reject:
            ret.status = "REJECTED"
            ret.save(update_fields=["status", "updated_at"])

            ret.order_item.item_status = "RETURN_REJECTED"
            ret.order_item.save(update_fields=["item_status"])

        OrderStatusHistory.objects.create(
            order=return_request.order,
            status=return_request.order.order_status,
            note=rejection_note or f"Return rejected for {returns_to_reject.count()} item(s).",
        )

    messages.success(request, "Selected return requests rejected.")
    return redirect("admin_return_detail", return_id=return_id)


@login_required(login_url="user_login")
def apply_coupon(request):
    if request.method != "POST":
        return redirect("cart_page")

    next_url = request.POST.get("next") or "cart_page"

    code = request.POST.get("code", "").strip()

    source = request.POST.get("source", "cart")

    if source == "buy_now":
        buy_now_data = request.session.get("buy_now")

        if not buy_now_data:
            messages.error(request, "Buy now session expired.")
            return redirect("product_list")

        variant = get_object_or_404(
            ProductVariant, id=buy_now_data["variant_id"], status="ACTIVE"
        )

        quantity = int(buy_now_data["quantity"])
        subtotal = variant.discounted_price * quantity

    else:
        cart_items = Cart.objects.filter(user=request.user).select_related("variant")
        subtotal = sum(item.variant.discounted_price * item.quantity for item in cart_items)

    coupon, discount, error = validate_coupon(code, request.user, subtotal)

    if error:
        messages.error(request, error)
        return redirect(next_url)

    request.session["applied_coupon"] = {
        "code": coupon.code,
        "discount": str(discount),
    }
    request.session.modified = True

    messages.success(request, f'Coupon "{coupon.code}" applied. You saved ₹{discount}.')

    return redirect(next_url)


@login_required(login_url="user_login")
def remove_coupon(request):
    if request.method == "POST":
        request.session.pop("applied_coupon", None)
        request.session.modified = True
        messages.success(request, "Coupon removed.")

    next_url = request.POST.get("next")

    if next_url:
        return redirect(next_url)

    return redirect("cart_page")