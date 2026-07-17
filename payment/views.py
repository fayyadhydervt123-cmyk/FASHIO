from decimal import Decimal
from types import SimpleNamespace

import razorpay

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from orders.models import Order, OrderStatusHistory
from products.models import Cart
from orders.views import GST_RATE, apply_referral_reward
from user.models import Wallet


def _get_razorpay_client():
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


@csrf_exempt
@require_POST
def razorpay_payment_success(request):
    razorpay_payment_id = request.POST.get("razorpay_payment_id")
    razorpay_order_id = request.POST.get("razorpay_order_id")
    razorpay_signature = request.POST.get("razorpay_signature")

    pending = request.session.get("pending_razorpay_order")

    if not razorpay_payment_id or not razorpay_order_id or not razorpay_signature:
        return _go_to_failure(request, "Payment was not completed.")

    if not pending or pending.get("razorpay_order_id") != razorpay_order_id:
        return _go_to_failure(request, "Payment session expired. Please try again.")

    client = _get_razorpay_client()

    try:
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
            }
        )
    except razorpay.errors.SignatureVerificationError:
        return _go_to_failure(request, "Payment verification failed.")

    order_id = pending.get("order_id")

    # Re-authenticate if session was lost across the redirect.
    if not request.user.is_authenticated:
        try:
            razorpay_order = client.order.fetch(razorpay_order_id)
            user_id = razorpay_order.get("notes", {}).get("user_id")
            if user_id:
                User = get_user_model()
                user = User.objects.get(id=user_id)
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        except Exception:
            return _go_to_failure(request, "Unable to confirm payment. Please contact support.")

    order = get_object_or_404(Order, order_id=order_id, user=request.user)

    if order.payment_status == "PAID":
        return redirect("order_success", order_id=order.order_id)

    request.session["pending_razorpay_order"] = {
        "razorpay_order_id": razorpay_order["id"],
        "order_id": str(order.order_id),
        "source": source,
    }

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)

        order.payment_status = "PAID"
        order.order_status = "PLACED"
        order.save(update_fields=["payment_status", "order_status"])

        for item in order.items.all():
            if item.variant:
                item.variant.stock -= item.quantity
                item.variant.save(update_fields=["stock"])

        payment = order.payment
        payment.payment_status = "PAID"
        payment.transaction_id = razorpay_payment_id
        payment.save(update_fields=["payment_status", "transaction_id"])

        OrderStatusHistory.objects.create(
            order=order, status="PLACED", note="Payment confirmed via Razorpay."
        )
        apply_referral_reward(order)

    source = pending.get("source", "cart")
    if source == "buy_now":
        request.session.pop("buy_now", None)
    else:
        Cart.objects.filter(user=request.user).delete()

    request.session.pop("pending_razorpay_order", None)
    request.session.modified = True

    messages.success(request, "Order placed successfully.")
    return redirect("order_success", order_id=order.order_id)


def _go_to_failure(request, message):
    messages.error(request, message)

    pending = request.session.pop("pending_razorpay_order", None)
    order_id = pending.get("order_id") if pending else None

    request.session["last_failed_order_id"] = order_id
    request.session.modified = True

    return redirect("payment_failure_page")


@csrf_exempt
@require_POST
def razorpay_payment_failure(request):
    """Fired by the ondismiss handler when the user closes the popup manually."""
    return _go_to_failure(request, "Payment was cancelled.")


@login_required(login_url="user_login")
def retry_payment(request, order_id):
    order = get_object_or_404(
        Order,
        order_id=order_id,
        user=request.user,
        payment_method="RAZORPAY",
        payment_status="PENDING",
    )

    if order.order_status != "PENDING":
        messages.error(request, "This order can no longer be retried.")
        return redirect("user_order_detail", order_id=order.order_id)

    amount_in_paise = int((order.total_amount * 100).quantize(Decimal("1")))

    client = _get_razorpay_client()

    try:
        razorpay_order = client.order.create(
            {
                "amount": amount_in_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "purpose": "ORDER_PAYMENT_RETRY",
                    "user_id": str(request.user.id),
                    "order_id": str(order.order_id),
                },
            }
        )
    except Exception:
        messages.error(request, "Unable to start payment. Please try again.")
        return redirect("user_order_detail", order_id=order.order_id)

    request.session["pending_razorpay_order"] = {
        "razorpay_order_id": razorpay_order["id"],
        "order_id": str(order.order_id),
    }
    request.session.modified = True

    cart_items_display = []
    for item in order.items.all():
        first_image = item.variant.images.first() if item.variant else None
        cart_items_display.append(
            SimpleNamespace(
                product=item.product,
                variant=item.variant,
                quantity=item.quantity,
                thumbnail=first_image.image.url if first_image else None,
                unit_price_amount=item.price,
                subtotal_amount=item.subtotal,
            )
        )

    wallet, _ = Wallet.objects.get_or_create(user=request.user)

    return render(
        request,
        "checkout/payment_method.html",
        {
            "open_razorpay": True,
            "razorpay_key_id": settings.RAZORPAY_KEY_ID,
            "razorpay_order_id": razorpay_order["id"],
            "amount_in_paise": amount_in_paise,
            "selected_address": order.address,
            "subtotal": order.subtotal,
            "gst_amount": order.tax_amount,
            "gst_rate": GST_RATE * 100,
            "delivery_fee": order.delivery_fee,
            "total_payable": order.total_amount,
            "wallet_balance": wallet.balance,
            "checkout_source": "retry",
            "cart_items": cart_items_display,
        },
    )


def payment_failure_page(request):
    order_id = request.session.pop("last_failed_order_id", None)
    request.session.modified = True

    order = None
    if order_id and request.user.is_authenticated:
        order = Order.objects.filter(order_id=order_id, user=request.user).first()

    return render(request, "orders/order_failure.html", {"order": order})