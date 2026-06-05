from types import SimpleNamespace #Creates simple objects that let attach attributes dynamically
from django.shortcuts import render, redirect, get_object_or_404 #Fetches a DB object, auto-returns 404 page if not found
from django.contrib.auth.decorators import login_required
from django.contrib import messages 
from decimal import Decimal, ROUND_HALF_UP
from products.models import Cart, ProductVariant, Product, Category
from user.models import Address
from .models import Order, OrderItem, Payment, OrderStatusHistory
from django.core.paginator import Paginator
from django.db.models import Q, Prefetch, Sum, Count
from django.db import transaction
from django.utils import timezone
from django.template.loader import render_to_string
from weasyprint import HTML
from django.http import HttpResponse

MAX_QUANTITY_PER_ORDER = 5
TAX_PERCENTAGE = Decimal("18")
DISCOUNT_PERCENTAGE = Decimal("3")

def calculate_checkout_totals(subtotal):
    delivery_fee = Decimal("0.00")

    tax_amount = (
        subtotal * TAX_PERCENTAGE / Decimal("100")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    discount_amount = (
        subtotal * DISCOUNT_PERCENTAGE / Decimal("100")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    total_payable = (
        subtotal + delivery_fee + tax_amount - discount_amount
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "delivery_fee": delivery_fee,
        "tax_percentage": TAX_PERCENTAGE,
        "discount_percentage": DISCOUNT_PERCENTAGE,
        "tax_amount": tax_amount,
        "discount_amount": discount_amount,
        "total_payable": total_payable,
    }


#Handles two purchase flows in one view
@login_required(login_url='user_login')
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
            status="ACTIVE"
        )

        quantity = int(buy_now_data.get("quantity", 1))

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

        cart_items.append(checkout_item)

        subtotal += item_subtotal
        total_items += quantity
        checkout_source = "buy_now"

    else:
        cart_queryset = (
            Cart.objects
            .filter(user=request.user)
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
            item.subtotal_amount = item.variant.discounted_price * item.quantity

            subtotal += item.subtotal_amount
            total_items += item.quantity

            cart_items.append(item)

        checkout_source = "cart"

    addresses = Address.objects.filter(user=request.user).order_by("-created_at")
    selected_address = addresses.first()

    totals = calculate_checkout_totals(subtotal)

    delivery_fee = totals["delivery_fee"]
    tax_percentage = totals["tax_percentage"]
    discount_percentage = totals["discount_percentage"]
    tax_amount = totals["tax_amount"]
    discount_amount = totals["discount_amount"]
    total_payable = totals["total_payable"]

    return render(request, "checkout/checkout.html", {
        "cart_items": cart_items,
        "addresses": addresses,
        "selected_address": selected_address,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "tax_amount": tax_amount,
        "delivery_fee": delivery_fee,
        "total_payable": total_payable,
        "total_items": total_items,
        "checkout_source": checkout_source,
        "tax_percentage": tax_percentage,
        "discount_percentage": discount_percentage,
    })


@login_required(login_url='user_login')
def payment_method(request):
    source = request.GET.get('source', 'cart')
    address_id = request.GET.get('address_id')

    if not address_id:
        messages.error(request, "Please select a delivery address.")
        return redirect("checkout_page")

    selected_address = get_object_or_404(
        Address,
        id=address_id,
        user=request.user
    )

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
            status="ACTIVE"
        )

        quantity = int(buy_now_data.get("quantity", 1))

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

        cart_items.append(checkout_item)
        subtotal += item_subtotal
        total_items += quantity

        checkout_source = "buy_now"

    else:
        cart_queryset = (
            Cart.objects
            .filter(user=request.user)
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
            item.subtotal_amount = item.variant.discounted_price * item.quantity

            subtotal += item.subtotal_amount
            total_items += item.quantity

            cart_items.append(item)

        checkout_source = "cart"

    totals = calculate_checkout_totals(subtotal)

    delivery_fee = totals["delivery_fee"]
    tax_percentage = totals["tax_percentage"]
    discount_percentage = totals["discount_percentage"]
    tax_amount = totals["tax_amount"]
    discount_amount = totals["discount_amount"]
    total_payable = totals["total_payable"]

    return render(request, "checkout/payment_method.html", {
        "cart_items": cart_items,
        "selected_address": selected_address,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "tax_amount": tax_amount,
        "delivery_fee": delivery_fee,
        "total_payable": total_payable,
        "total_items": total_items,
        "checkout_source": checkout_source,
        "tax_percentage": tax_percentage,
        "discount_percentage": discount_percentage,
    })


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

    if payment_method != "COD":
        messages.error(request, "Only Cash On Delivery is available now.")
        return redirect("payment_method")

    address = get_object_or_404(
        Address,
        id=address_id,
        user=request.user
    )

    order_items_data = []
    subtotal = Decimal("0.00")

    if source == "buy_now":
        buy_now_data = request.session.get("buy_now")

        if not buy_now_data:
            messages.error(request, "Buy now session expired.")
            return redirect("product_list")

        variant = get_object_or_404(
            ProductVariant,
            id=buy_now_data.get("variant_id"),
            status="ACTIVE"
        )

        quantity = int(buy_now_data.get("quantity", 1))

        if variant.stock < quantity:
            messages.error(request, "Not enough stock available.")
            return redirect("product_detail", product_id=variant.product.id)

        price = variant.discounted_price
        item_total = price * quantity

        order_items_data.append({
            "product": variant.product,
            "variant": variant,
            "quantity": quantity,
            "price": price,
            "subtotal": item_total,
        })

        subtotal += item_total

    else:
        cart_items = (
            Cart.objects
            .filter(user=request.user)
            .select_related("product", "variant")
        )

        if not cart_items.exists():
            messages.error(request, "Your cart is empty.")
            return redirect("cart_page")

        for item in cart_items:
            if item.variant.stock < item.quantity:
                messages.error(request, f"Not enough stock for {item.product.name}.")
                return redirect("cart_page")

            price = item.variant.discounted_price
            item_total = price * item.quantity

            order_items_data.append({
                "product": item.product,
                "variant": item.variant,
                "quantity": item.quantity,
                "price": price,
                "subtotal": item_total,
            })

            subtotal += item_total

    totals = calculate_checkout_totals(subtotal)

    delivery_fee = totals["delivery_fee"]
    tax_amount = totals["tax_amount"]
    discount_amount = totals["discount_amount"]
    total_amount = totals["total_payable"]

    order = Order.objects.create(
        user=request.user,
        address=address,
        payment_method="COD",
        payment_status="PENDING",
        order_status="PLACED",
        subtotal=subtotal,
        discount_amount=discount_amount,
        tax_amount=tax_amount,
        delivery_fee=delivery_fee,
        total_amount=total_amount,
    )

    order.order_id = f"ORD-{order.id:06d}"
    order.save(update_fields=["order_id"])

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
            subtotal=item["subtotal"],
        )

        item["variant"].stock -= item["quantity"]
        item["variant"].save()

    Payment.objects.create(
        order=order,
        payment_method="COD",
        payment_status="PENDING",
        amount=total_amount,
    )

    OrderStatusHistory.objects.create(
        order=order,
        status="PLACED",
        note="Order placed successfully."
    )

    if source == "buy_now":
        request.session.pop("buy_now", None)
        request.session.modified = True
    else:
        Cart.objects.filter(user=request.user).delete()

    messages.success(request, "Order placed successfully.")
    return redirect("order_success", order_id=order.order_id)

@login_required(login_url='user_login')
def order_success(request, order_id):
    order = get_object_or_404(
        Order.objects
        .select_related("user", "address")
        .prefetch_related(
            "items",
            "items__product",
            "items__variant",
            "items__variant__images"
        ),
        order_id=order_id,
        user=request.user
    )

    return render(request, "orders/order_success.html", {
        "order": order
    })


@login_required(login_url="admin_login")
def admin_order_list(request):
    orders_queryset = (
        Order.objects
        .select_related("user", "address")
        .order_by("-created_at")
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
            Q(order_id__icontains=query) |
            Q(user__fullname__icontains=query) |
            Q(user__email__icontains=query) |
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query)
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
            orders_queryset = orders_queryset.filter(total_amount__gte=min_amount_decimal)
        except InvalidOperation:
            messages.error(request, "Invalid minimum amount.")

    if max_amount:
        try:
            max_amount_decimal = Decimal(max_amount)
            orders_queryset = orders_queryset.filter(total_amount__lte=max_amount_decimal)
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
        billable_items = order.items.filter(
            item_status__in=["ACTIVE", "RETURN_REQUESTED"]
        )

        cancelled_items = order.items.filter(item_status="CANCELLED")
        returned_items = order.items.filter(item_status="RETURNED")

        order.billable_subtotal = sum(item.subtotal for item in billable_items)
        order.cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
        order.returned_subtotal = sum(item.subtotal for item in returned_items)

        order.cancelled_items_count = cancelled_items.count()
        order.returned_items_count = returned_items.count()

        if order.billable_subtotal > 0:
            totals = calculate_checkout_totals(order.billable_subtotal)

            order.display_delivery_fee = totals["delivery_fee"]
            order.display_tax_amount = totals["tax_amount"]
            order.display_discount_amount = totals["discount_amount"]
            order.display_total_amount = totals["total_payable"]
        else:
            order.display_delivery_fee = Decimal("0.00")
            order.display_tax_amount = Decimal("0.00")
            order.display_discount_amount = Decimal("0.00")
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
        Order.objects
        .select_related(
            "user",
            "address",
            "payment"
        )
        .prefetch_related(
            "items",
            "items__product",
            "items__variant",
            "items__variant__images",
            "status_history"
        ),
        id=order_id
    )

    try:
        payment = order.payment
    except Exception:
        payment = None

    status_history = order.status_history.all().order_by("created_at")

    billable_items = order.items.filter(
        item_status__in=["ACTIVE", "RETURN_REQUESTED"]
    )

    cancelled_items = order.items.filter(item_status="CANCELLED")
    returned_items = order.items.filter(item_status="RETURNED")

    billable_subtotal = sum(item.subtotal for item in billable_items)
    cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
    returned_subtotal = sum(item.subtotal for item in returned_items)

    if billable_subtotal > 0:
        totals = calculate_checkout_totals(billable_subtotal)

        display_delivery_fee = totals["delivery_fee"]
        display_tax_amount = totals["tax_amount"]
        display_discount_amount = totals["discount_amount"]
        display_total_amount = totals["total_payable"]
    else:
        display_delivery_fee = Decimal("0.00")
        display_tax_amount = Decimal("0.00")
        display_discount_amount = Decimal("0.00")
        display_total_amount = Decimal("0.00")

    return render(request, "orders/order_detail.html", {
        "order": order,
        "payment": payment,
        "status_history": status_history,
        "billable_subtotal": billable_subtotal,
        "cancelled_subtotal": cancelled_subtotal,
        "returned_subtotal": returned_subtotal,
        "display_delivery_fee": display_delivery_fee,
        "display_tax_amount": display_tax_amount,
        "display_discount_amount": display_discount_amount,
        "display_total_amount": display_total_amount,
    })

@login_required(login_url="admin_login")
def admin_change_order_status(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if request.method == "POST":
        new_status = request.POST.get("order_status")
        note = request.POST.get("note", "").strip()

        valid_statuses = [choice[0] for choice in Order.ORDER_STATUS_CHOICES]

        if new_status not in valid_statuses:
            messages.error(request, "Invalid order status.")
            return redirect("admin_change_order_status", order_id=order.id)

        if order.order_status in ["DELIVERED", "CANCELLED"]:
            messages.error(request, "Delivered or cancelled orders cannot be changed.")
            return redirect("admin_order_detail", order_id=order.id)

        if order.order_status == new_status:
            messages.info(request, "Order status is already the same.")
            return redirect("admin_change_order_status", order_id=order.id)

        old_status = order.order_status
        order.order_status = new_status
        order.save()

        OrderStatusHistory.objects.create(
            order=order,
            status=new_status,
            note=note or f"Status changed from {old_status} to {new_status}."
        )

        messages.success(request, "Order status updated successfully.")
        return redirect("admin_order_detail", order_id=order.id)

    return render(request, "orders/change_order_status.html", {
        "order": order,
        "status_choices": Order.ORDER_STATUS_CHOICES,
    })

@login_required(login_url="admin_login")
def inventory_list(request):
    LOW_STOCK_LIMIT = 5

    query = request.GET.get("q", "").strip()
    stock_filter = request.GET.get("stock", "").strip()
    category_filter = request.GET.get("category", "").strip()

    products_queryset = (
        Product.objects
        .select_related(
            "subcategory",
            "subcategory__category"
        )
        .prefetch_related(
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.prefetch_related("images")
            )
        )
        .annotate(
            total_stock=Sum("variants__stock"),
            variant_count=Count("variants", distinct=True)
        )
        .order_by("-id")
    )

    # Search by product name or product id
    if query:
        products_queryset = products_queryset.filter(
            Q(name__icontains=query) |
            Q(id__icontains=query)
        )

    # Filter by category
    if category_filter:
        products_queryset = products_queryset.filter(
            subcategory__category_id=category_filter
        )

    # Filter by stock level
    if stock_filter == "out_of_stock":
        products_queryset = products_queryset.filter(
            total_stock__lte=0
        )

    elif stock_filter == "low_stock":
        products_queryset = products_queryset.filter(
            total_stock__gt=0,
            total_stock__lte=LOW_STOCK_LIMIT
        )

    elif stock_filter == "in_stock":
        products_queryset = products_queryset.filter(
            total_stock__gt=LOW_STOCK_LIMIT
        )

    # Stats cards should count all products, not only filtered products
    stats_queryset = (
        Product.objects
        .annotate(
            total_stock=Sum("variants__stock")
        )
    )

    total_products = stats_queryset.count()

    out_of_stock_count = stats_queryset.filter(
        total_stock__lte=0
    ).count()

    low_stock_count = stats_queryset.filter(
        total_stock__gt=0,
        total_stock__lte=LOW_STOCK_LIMIT
    ).count()


    paginator = Paginator(products_queryset, 5)
    page_number = request.GET.get("page")
    products = paginator.get_page(page_number)


    # Add thumbnail manually
    for product in products_queryset:
        product.thumbnail = None

        first_variant = product.variants.first()

        if first_variant:
            first_image = first_variant.images.first()
            if first_image:
                product.thumbnail = first_image.image.url

        if product.total_stock is None:
            product.total_stock = 0

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
        Order.objects
        .filter(user=request.user)
        .prefetch_related("items", "items__variant")
        .order_by("-created_at")
    )

    if query:
        orders = orders.filter(
            Q(order_id__icontains=query) |
            Q(items__product_name__icontains=query) |
            Q(order_status__icontains=query)
        ).distinct()

    for order in orders:
        billable_items = order.items.filter(
            item_status__in=["ACTIVE", "RETURN_REQUESTED"]
        )

        active_items = order.items.filter(item_status="ACTIVE")
        cancelled_items = order.items.filter(item_status="CANCELLED")
        return_requested_items = order.items.filter(item_status="RETURN_REQUESTED")
        returned_items = order.items.filter(item_status="RETURNED")

        order.has_active_items = active_items.exists()

        order.billable_subtotal = sum(item.subtotal for item in billable_items)
        order.cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
        order.returned_subtotal = sum(item.subtotal for item in returned_items)

        if order.billable_subtotal > 0:
            totals = calculate_checkout_totals(order.billable_subtotal)

            order.display_delivery_fee = totals["delivery_fee"]
            order.display_tax_amount = totals["tax_amount"]
            order.display_discount_amount = totals["discount_amount"]
            order.display_total_amount = totals["total_payable"]
        else:
            order.display_delivery_fee = Decimal("0.00")
            order.display_tax_amount = Decimal("0.00")
            order.display_discount_amount = Decimal("0.00")
            order.display_total_amount = Decimal("0.00")

    return render(request, "orders/user_orders.html", {
        "orders": orders,
        "query": query,
    })

@login_required(login_url="user_login")
def user_order_detail(request, order_id):
    order = get_object_or_404(
        Order.objects
        .select_related("user", "address")
        .prefetch_related(
            "items",
            "items__product",
            "items__variant",
            "items__variant__images",
            "status_history",
        ),
        order_id=order_id,
        user=request.user
    )

    status_history = order.status_history.all().order_by("created_at")

    can_cancel = order.order_status in ["PENDING", "PLACED"]

    can_request_return = (
        order.order_status == "DELIVERED"
        and order.items.filter(item_status="ACTIVE").exists()
    )

    cancelled_item_ids = request.session.pop(
        f"cancel_success_items_{order.order_id}",
        []
    )

    cancelled_success_items = OrderItem.objects.filter(
        id__in=cancelled_item_ids,
        order=order,
        item_status="CANCELLED"
    )

    return_item_ids = request.session.pop(
        f"return_success_items_{order.order_id}",
        []
    )

    return_success_items = OrderItem.objects.filter(
        id__in=return_item_ids,
        order=order,
        item_status="RETURN_REQUESTED"
    )

    show_return_success_modal = return_success_items.exists()

    show_cancel_success_modal = cancelled_success_items.exists()

    billable_items = order.items.filter(
        item_status__in=["ACTIVE", "RETURN_REQUESTED"]
    )

    cancelled_items = order.items.filter(item_status="CANCELLED")
    returned_items = order.items.filter(item_status="RETURNED")

    active_subtotal = sum(item.subtotal for item in billable_items)
    cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
    returned_subtotal = sum(item.subtotal for item in returned_items)

    if active_subtotal > 0:
        totals = calculate_checkout_totals(active_subtotal)

        display_delivery_fee = totals["delivery_fee"]
        display_tax_amount = totals["tax_amount"]
        display_discount_amount = totals["discount_amount"]
        display_total_amount = totals["total_payable"]
    else:
        display_delivery_fee = Decimal("0.00")
        display_tax_amount = Decimal("0.00")
        display_discount_amount = Decimal("0.00")
        display_total_amount = Decimal("0.00")

    return render(request, "orders/user_order_detail.html", {
        "order": order,
        "status_history": status_history,
        "can_cancel": can_cancel,
        "can_request_return": can_request_return,
        "cancelled_success_items": cancelled_success_items,
        "show_cancel_success_modal": show_cancel_success_modal,

        "active_subtotal": active_subtotal,
        "cancelled_subtotal": cancelled_subtotal,
        "display_delivery_fee": display_delivery_fee,
        "display_tax_amount": display_tax_amount,
        "display_discount_amount": display_discount_amount,
        "display_total_amount": display_total_amount,

        "return_success_items": return_success_items,
        "show_return_success_modal": show_return_success_modal,
        "returned_subtotal": returned_subtotal,
    })

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
        id__in=selected_items,
        order=order,
        item_status="ACTIVE"
    )

    if not valid_items.exists():
        messages.error(request, "No valid items selected.")
        return redirect("user_order_detail", order_id=order.order_id)

    request.session[f"cancel_items_{order.order_id}"] = [str(item.id) for item in valid_items]
    request.session.modified = True

    return redirect("user_cancel_order_page", order_id=order.order_id)


@login_required(login_url="user_login")
def user_cancel_order_page(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("address").prefetch_related(
            "items",
            "items__variant",
            "items__variant__images",
        ),
        order_id=order_id,
        user=request.user
    )

    selected_item_ids = request.session.get(f"cancel_items_{order.order_id}", [])

    selected_items = OrderItem.objects.filter(
        id__in=selected_item_ids,
        order=order,
        item_status="ACTIVE"
    )

    if not selected_items.exists():
        messages.error(request, "Please select items to cancel.")
        return redirect("user_order_detail", order_id=order.order_id)

    estimated_refund = sum(item.subtotal for item in selected_items)

    return render(request, "orders/cancel_order.html", {
        "order": order,
        "selected_items": selected_items,
        "estimated_refund": estimated_refund,
        "cancellation_fee": 0,
        "cancellation_reasons": CANCELLATION_REASONS,
    })


@login_required(login_url="user_login")
def user_confirm_cancel_items(request, order_id):
    if request.method != "POST":
        return redirect("user_cancel_order_page", order_id=order_id)

    order = get_object_or_404(Order, order_id=order_id, user=request.user)

    selected_item_ids = request.session.get(f"cancel_items_{order.order_id}", [])

    selected_items = OrderItem.objects.filter(
        id__in=selected_item_ids,
        order=order,
        item_status="ACTIVE"
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
        for item in selected_items:
            item.item_status = "CANCELLED"
            item.cancel_reason = reason
            item.cancel_comment = comment
            item.cancelled_at = timezone.now()
            item.save(update_fields=[
                "item_status",
                "cancel_reason",
                "cancel_comment",
                "cancelled_at",
            ])

            if item.variant:
                item.variant.stock += item.quantity
                item.variant.save(update_fields=["stock"])

        active_items_left = order.items.filter(item_status="ACTIVE").exists()

        if not active_items_left:
            order.order_status = "CANCELLED"
            order.save(update_fields=["order_status"])

            OrderStatusHistory.objects.create(
                order=order,
                status="CANCELLED",
                note="Order cancelled by customer."
            )
        else:
            OrderStatusHistory.objects.create(
                order=order,
                status=order.order_status,
                note="Some items were cancelled by customer."
            )

    cancelled_item_ids_for_success = [str(item.id) for item in selected_items]

    request.session.pop(f"cancel_items_{order.order_id}", None)

    request.session[f"cancel_success_items_{order.order_id}"] = cancelled_item_ids_for_success
    request.session.modified = True

    return redirect("user_order_detail", order_id=order.order_id)

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
        id__in=selected_items,
        order=order,
        item_status="ACTIVE"
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
            "items",
            "items__variant",
            "items__variant__images",
        ),
        order_id=order_id,
        user=request.user
    )

    if order.order_status != "DELIVERED":
        messages.error(request, "Return is available only after delivery.")
        return redirect("user_order_detail", order_id=order.order_id)

    selected_item_ids = request.session.get(f"return_items_{order.order_id}", [])

    selected_items = OrderItem.objects.filter(
        id__in=selected_item_ids,
        order=order,
        item_status="ACTIVE"
    )

    if not selected_items.exists():
        messages.error(request, "Please select items to return.")
        return redirect("user_order_detail", order_id=order.order_id)

    estimated_refund = sum(item.subtotal for item in selected_items)

    return render(request, "orders/return_order.html", {
        "order": order,
        "selected_items": selected_items,
        "estimated_refund": estimated_refund,
        "restocking_fee": 0,
        "return_reasons": RETURN_REASONS,
    })


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
        id__in=selected_item_ids,
        order=order,
        item_status="ACTIVE"
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
        for item in selected_items:
            item.item_status = "RETURN_REQUESTED"
            item.return_reason = reason
            item.return_comment = comment
            item.return_requested_at = timezone.now()
            item.save(update_fields=[
                "item_status",
                "return_reason",
                "return_comment",
                "return_requested_at",
            ])

        OrderStatusHistory.objects.create(
            order=order,
            status=order.order_status,
            note="Return request submitted by customer."
        )

    return_item_ids_for_success = [str(item.id) for item in selected_items]

    request.session.pop(f"return_items_{order.order_id}", None)
    request.session[f"return_success_items_{order.order_id}"] = return_item_ids_for_success
    request.session.modified = True

    return redirect("user_order_detail", order_id=order.order_id)

@login_required(login_url="user_login")
def download_invoice(request, order_id):
    order = get_object_or_404(
        Order.objects
        .select_related("user", "address")
        .prefetch_related(
            "items",
            "items__product",
            "items__variant",
        ),
        order_id=order_id,
        user=request.user
    )

    billable_items = order.items.filter(
        item_status__in=["ACTIVE", "RETURN_REQUESTED"]
    )

    cancelled_items = order.items.filter(item_status="CANCELLED")
    returned_items = order.items.filter(item_status="RETURNED")

    billable_subtotal = sum(item.subtotal for item in billable_items)
    cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
    returned_subtotal = sum(item.subtotal for item in returned_items)

    if billable_subtotal > 0:
        totals = calculate_checkout_totals(billable_subtotal)

        display_delivery_fee = totals["delivery_fee"]
        display_tax_amount = totals["tax_amount"]
        display_discount_amount = totals["discount_amount"]
        display_total_amount = totals["total_payable"]
    else:
        display_delivery_fee = Decimal("0.00")
        display_tax_amount = Decimal("0.00")
        display_discount_amount = Decimal("0.00")
        display_total_amount = Decimal("0.00")

    html_string = render_to_string("orders/invoice.html", {
        "order": order,
        "billable_subtotal": billable_subtotal,
        "cancelled_subtotal": cancelled_subtotal,
        "returned_subtotal": returned_subtotal,
        "display_delivery_fee": display_delivery_fee,
        "display_tax_amount": display_tax_amount,
        "display_discount_amount": display_discount_amount,
        "display_total_amount": display_total_amount,
    })

    pdf_file = HTML(
        string=html_string,
        base_url=request.build_absolute_uri("/")
    ).write_pdf()

    response = HttpResponse(pdf_file, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="invoice_{order.order_id}.pdf"'
    )

    return response