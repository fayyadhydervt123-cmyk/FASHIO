import calendar
import csv
import json
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from orders.models import Order, OrderItem, ReturnRequest
from products.models import Category, Product, ProductVariant
from user.models import Wallet

# Custom user model
User = get_user_model()

# Order item statuses that count as billable revenue (i.e. not cancelled
# or fully returned/rejected).
BILLABLE_ITEM_STATUSES = ["ACTIVE", "RETURN_REQUESTED", "RETURN_APPROVED"]

PAYMENT_METHOD_LABELS = {
    "COD": "Cash on Delivery",
    "WALLET": "Wallet",
    "RAZORPAY": "Razorpay",
}


def is_admin(user):
    """Return True if the user is authenticated and has admin privileges."""
    return user.is_authenticated and (user.is_staff or user.is_superuser)


@never_cache
def admin_login(request):
    """Handle admin login form display and authentication."""
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("admin_dashboard")

    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")

        user = authenticate(request, username=email, password=password)

        if user is not None and user.is_staff:
            login(request, user)
            return redirect("admin_dashboard")
        messages.error(request, "Invalid credentials or not authorized")

    return render(request, "admin_panel/login.html")


def _get_range_bounds(range_key, now):
    """Return (start, end, prev_start, prev_end) as aware datetimes."""
    if range_key == "week":
        start = now - timedelta(days=7)
    elif range_key == "year":
        start = now - timedelta(days=365)
    else:
        range_key = "month"
        start = now - timedelta(days=30)

    span = now - start
    prev_start = start - span
    prev_end = start

    return start, now, prev_start, prev_end


def _percent_change(current, previous):
    """Return the percentage change between two numeric values."""
    if not previous:
        return 100.0 if current else 0.0
    return float((current - previous) / previous * 100)


@never_cache
@user_passes_test(is_admin, login_url="admin_login")
def admin_dashboard(request):
    """Render the admin dashboard with revenue, order, and customer stats."""
    now = timezone.now()
    range_key = request.GET.get("range", "month")
    if range_key not in ("week", "month", "year"):
        range_key = "month"

    start, end, prev_start, prev_end = _get_range_bounds(range_key, now)

    # -- Revenue + orders (current period) — billable items only --
    current_items = OrderItem.objects.filter(
        item_status__in=BILLABLE_ITEM_STATUSES,
        order__created_at__gte=start,
        order__created_at__lt=end,
    )

    total_revenue = current_items.aggregate(
        total=Coalesce(Sum("subtotal"), Value(0, output_field=DecimalField()))
    )["total"]

    total_orders = Order.objects.filter(
        created_at__gte=start, created_at__lt=end
    ).count()

    avg_order_value = (total_revenue / total_orders) if total_orders else 0

    # -- Previous period (for % change) --
    prev_items = OrderItem.objects.filter(
        item_status__in=BILLABLE_ITEM_STATUSES,
        order__created_at__gte=prev_start,
        order__created_at__lt=prev_end,
    )
    prev_revenue = prev_items.aggregate(
        total=Coalesce(Sum("subtotal"), Value(0, output_field=DecimalField()))
    )["total"]

    prev_orders = Order.objects.filter(
        created_at__gte=prev_start, created_at__lt=prev_end
    ).count()

    prev_aov = (prev_revenue / prev_orders) if prev_orders else 0

    revenue_change = round(_percent_change(total_revenue, prev_revenue), 1)
    orders_change = round(_percent_change(total_orders, prev_orders), 1)
    aov_change = round(_percent_change(avg_order_value, prev_aov), 1)

    # -- Orders pending — current snapshot, not time filtered --
    pending_orders = Order.objects.filter(order_status="PENDING").count()

    # -- New customers — signups in range vs previous range --
    new_customers = User.objects.filter(
        is_staff=False,
        is_superuser=False,
        created_at__gte=start,
        created_at__lt=end,
    ).count()

    prev_new_customers = User.objects.filter(
        is_staff=False,
        is_superuser=False,
        created_at__gte=prev_start,
        created_at__lt=prev_end,
    ).count()

    new_customers_change = round(
        _percent_change(new_customers, prev_new_customers), 1
    )

    # -- Active customers — distinct users with an order in range --
    active_customers = (
        Order.objects.filter(created_at__gte=start, created_at__lt=end)
        .values("user_id")
        .distinct()
        .count()
    )

    prev_active_customers = (
        Order.objects.filter(created_at__gte=prev_start, created_at__lt=prev_end)
        .values("user_id")
        .distinct()
        .count()
    )

    active_customers_change = round(
        _percent_change(active_customers, prev_active_customers), 1
    )

    # -- Sales chart — by day (week/month) or by month (year) --
    if range_key == "year":
        chart_rows = (
            current_items.annotate(bucket=TruncMonth("order__created_at"))
            .values("bucket")
            .annotate(total=Sum("subtotal"))
            .order_by("bucket")
        )
        chart_labels = [
            calendar.month_abbr[row["bucket"].month].upper() for row in chart_rows
        ]
    else:
        chart_rows = (
            current_items.annotate(bucket=TruncDate("order__created_at"))
            .values("bucket")
            .annotate(total=Sum("subtotal"))
            .order_by("bucket")
        )
        chart_labels = [row["bucket"].strftime("%b %d") for row in chart_rows]

    chart_values = [float(row["total"]) for row in chart_rows]

    # -- Recent orders — latest 5, unaffected by time range --
    recent_orders = Order.objects.select_related("user").order_by("-created_at")[:5]

    # -- Best selling categories — all-time revenue share, top 5 --
    category_sales_qs = (
        OrderItem.objects.filter(
            item_status__in=BILLABLE_ITEM_STATUSES,
            product__isnull=False,
            product__subcategory__isnull=False,
        )
        .values(
            "product__subcategory__category__id",
            "product__subcategory__category__name",
        )
        .annotate(category_revenue=Sum("subtotal"))
        .order_by("-category_revenue")
    )

    total_category_revenue = (
        sum(row["category_revenue"] for row in category_sales_qs) or 0
    )

    best_selling_categories = []
    for row in category_sales_qs[:5]:
        revenue = row["category_revenue"] or 0
        share = (
            (revenue / total_category_revenue * 100)
            if total_category_revenue
            else 0
        )
        best_selling_categories.append(
            {
                "name": row["product__subcategory__category__name"],
                "revenue": revenue,
                "share": round(share, 1),
            }
        )

    # Bar width relative to the top category, so the largest bar is full width.
    if best_selling_categories:
        max_share = best_selling_categories[0]["share"] or 1
        for cat in best_selling_categories:
            cat["bar_width"] = (
                round((cat["share"] / max_share) * 100, 1) if max_share else 0
            )

    context = {
        "range_key": range_key,
        "total_revenue": total_revenue,
        "revenue_change": revenue_change,
        "total_orders": total_orders,
        "orders_change": orders_change,
        "avg_order_value": avg_order_value,
        "aov_change": aov_change,
        "pending_orders": pending_orders,
        "new_customers": new_customers,
        "new_customers_change": new_customers_change,
        "active_customers": active_customers,
        "active_customers_change": active_customers_change,
        "chart_labels": json.dumps(chart_labels),
        "chart_values": json.dumps(chart_values),
        "recent_orders": recent_orders,
        "best_selling_categories": best_selling_categories,
    }

    return render(request, "admin_panel/dashboard.html", context)


@never_cache
@user_passes_test(is_admin, login_url="admin_login")
def admin_logout(request):
    """Log the admin user out and redirect to the login page."""
    logout(request)
    return redirect("admin_login")


@never_cache
@user_passes_test(is_admin, login_url="admin_login")
def admin_customerlist(request):
    """List, search, and filter regular (non-staff) customers."""
    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    date_filter = request.GET.get("date", "")

    userlist = User.objects.filter(is_staff=False, is_superuser=False)

    # Search
    if search_query:
        userlist = userlist.filter(
            Q(fullname__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(phone__icontains=search_query)
        )

    # Status filter
    if status_filter == "active":
        userlist = userlist.filter(is_active=True)
    elif status_filter == "inactive":
        userlist = userlist.filter(is_active=False)

    # Date filter
    today = timezone.now().date()

    if date_filter == "today":
        userlist = userlist.filter(date_joined__date=today)
    elif date_filter == "last_7_days":
        userlist = userlist.filter(date_joined__gte=timezone.now() - timedelta(days=7))
    elif date_filter == "last_30_days":
        userlist = userlist.filter(date_joined__gte=timezone.now() - timedelta(days=30))

    userlist = userlist.order_by("-date_joined")

    # Pagination
    paginator = Paginator(userlist, 8)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "admin_panel/customers.html",
        {
            "userlist": page_obj,
            "search_query": search_query,
            "status_filter": status_filter,
            "date_filter": date_filter,
        },
    )


@never_cache
@user_passes_test(is_admin, login_url="admin_login")
def admin_user_details(request, user_id):
    """Show a single customer's profile, orders, and wallet details."""
    try:
        user = User.objects.get(id=user_id, is_staff=False, is_superuser=False)
    except User.DoesNotExist:
        messages.error(request, "User not found")
        return redirect("admin_customerlist")

    # Addresses
    addresses = user.addresses.all()

    # Orders (Order.user has no custom related_name, so the default reverse
    # accessor is order_set).
    orders = user.order_set.order_by("-created_at")
    recent_orders = orders[:5]
    total_orders = orders.count()
    total_spend = orders.filter(payment_status="PAID").aggregate(
        total=Sum("total_amount")
    )["total"] or 0

    # Wallet + transactions
    wallet, _ = Wallet.objects.get_or_create(user=user)
    recent_transactions = wallet.transactions.all()[:5]

    context = {
        "user_obj": user,
        "addresses": addresses,
        "recent_orders": recent_orders,
        "total_orders": total_orders,
        "total_spend": total_spend,
        "wallet": wallet,
        "recent_transactions": recent_transactions,
    }

    return render(request, "admin_panel/user_details.html", context)


@never_cache
@user_passes_test(is_admin, login_url="admin_login")
def toggle_user_status(request, user_id):
    """Toggle a customer's active/inactive status."""
    user = User.objects.get(id=user_id)

    user.is_active = not user.is_active
    user.save()

    return redirect("admin_user_details", user_id=user.id)


def _get_analytics_date_range(request, today):
    """Resolve the (date_from, date_to, range_filter) for the analytics view."""
    range_filter = request.GET.get("range", "").strip()

    if range_filter == "today":
        return today, today, range_filter
    if range_filter == "week":
        return today - timedelta(days=today.weekday()), today, range_filter
    if range_filter == "month":
        return today.replace(day=1), today, range_filter
    if range_filter == "year":
        return today.replace(month=1, day=1), today, range_filter

    # No quick-range selected — fall back to explicit date_from/date_to
    # (or a default 180-day window).
    range_filter = ""
    try:
        date_to = (
            date.fromisoformat(request.GET.get("date_to", ""))
            if request.GET.get("date_to")
            else today
        )
    except ValueError:
        date_to = today
    try:
        date_from = (
            date.fromisoformat(request.GET.get("date_from", ""))
            if request.GET.get("date_from")
            else date_to - timedelta(days=180)
        )
    except ValueError:
        date_from = date_to - timedelta(days=180)

    return date_from, date_to, range_filter


@user_passes_test(is_admin, login_url="admin_login")
def admin_analytics(request):
    """Render the sales report / analytics dashboard, with CSV export."""
    category_id = request.GET.get("category", "").strip()
    payment_method = request.GET.get("payment_method", "").strip()

    today = timezone.now().date()
    date_from, date_to, range_filter = _get_analytics_date_range(request, today)

    def get_qs(date_start, date_end):
        qs = OrderItem.objects.filter(
            item_status__in=BILLABLE_ITEM_STATUSES,
            order__created_at__date__gte=date_start,
            order__created_at__date__lte=date_end,
        ).select_related("order", "product__subcategory__category")
        if category_id:
            qs = qs.filter(product__subcategory__category_id=category_id)
        if payment_method:
            qs = qs.filter(order__payment_method=payment_method)
        return qs

    def get_summary(date_start, date_end):
        qs = get_qs(date_start, date_end)
        agg = qs.aggregate(
            revenue=Coalesce(Sum("subtotal"), Value(Decimal("0.00"))),
            orders=Count("order", distinct=True),
        )
        revenue, orders = agg["revenue"], agg["orders"]
        aov = (
            (revenue / orders).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if orders
            else Decimal("0.00")
        )
        refunds = ReturnRequest.objects.filter(
            status="REFUNDED",
            updated_at__date__gte=date_start,
            updated_at__date__lte=date_end,
        ).aggregate(total=Coalesce(Sum("refund_amount"), Value(Decimal("0.00"))))[
            "total"
        ]
        return revenue, orders, aov, refunds

    total_revenue, total_orders, avg_order_value, refunds_total = get_summary(
        date_from, date_to
    )

    period_days = (date_to - date_from).days
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=period_days)
    prev_revenue, prev_orders, prev_aov, prev_refunds = get_summary(
        prev_from, prev_to
    )

    def pct_change(cur, prev):
        if not prev:
            return None
        return float(
            ((cur - prev) / prev * 100).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        )

    revenue_change = pct_change(total_revenue, prev_revenue)
    orders_change = pct_change(Decimal(total_orders), Decimal(prev_orders))
    aov_change = pct_change(avg_order_value, prev_aov)
    refunds_change = pct_change(refunds_total, prev_refunds)

    qs = get_qs(date_from, date_to)

    bucket_func = (
        TruncDate("order__created_at")
        if period_days <= 31
        else TruncMonth("order__created_at")
    )
    date_format = "%b %d" if period_days <= 31 else "%b %Y"

    time_rows = list(
        qs.annotate(bucket=bucket_func)
        .values("bucket")
        .annotate(revenue=Sum("subtotal"), orders=Count("order", distinct=True))
        .order_by("bucket")
    )

    time_series_labels = [row["bucket"].strftime(date_format) for row in time_rows]
    time_series_revenue = [float(row["revenue"] or 0) for row in time_rows]
    time_series_orders = [row["orders"] for row in time_rows]

    category_rows = list(
        qs.values("product__subcategory__category__name")
        .annotate(revenue=Sum("subtotal"))
        .order_by("-revenue")
    )
    category_labels = [
        row["product__subcategory__category__name"] or "Uncategorized"
        for row in category_rows
    ]
    category_revenue = [float(row["revenue"] or 0) for row in category_rows]

    payment_rows = list(
        qs.values("order__payment_method")
        .annotate(revenue=Sum("subtotal"))
        .order_by("-revenue")
    )
    payment_labels = [
        PAYMENT_METHOD_LABELS.get(row["order__payment_method"], row["order__payment_method"])
        for row in payment_rows
    ]
    payment_revenue = [float(row["revenue"] or 0) for row in payment_rows]

    product_rows_qs = (
        qs.values(
            "product__id", "product__name", "product__subcategory__category__name"
        )
        .annotate(units_sold=Sum("quantity"), revenue=Sum("subtotal"))
        .order_by("-revenue")
    )

    paginator = Paginator(product_rows_qs, 8)
    product_page = paginator.get_page(request.GET.get("page"))

    product_rows = []
    for row in product_page:
        product = (
            Product.objects.filter(id=row["product__id"])
            .prefetch_related(
                Prefetch("variants", queryset=ProductVariant.objects.prefetch_related("images"))
            )
            .first()
        )
        thumbnail = None
        if product:
            first_variant = product.variants.first()
            if first_variant:
                first_image = first_variant.images.first()
                thumbnail = first_image.image.url if first_image else None
        product_rows.append(
            {
                "name": row["product__name"],
                "category": row["product__subcategory__category__name"] or "Uncategorized",
                "units_sold": row["units_sold"],
                "revenue": row["revenue"],
                "thumbnail": thumbnail,
            }
        )

    if request.GET.get("export") == "csv":
        return _export_analytics_csv(
            date_from=date_from,
            date_to=date_to,
            category_id=category_id,
            payment_method=payment_method,
            total_revenue=total_revenue,
            total_orders=total_orders,
            avg_order_value=avg_order_value,
            refunds_total=refunds_total,
            category_labels=category_labels,
            category_revenue=category_revenue,
            payment_labels=payment_labels,
            payment_revenue=payment_revenue,
            product_rows_qs=product_rows_qs,
        )

    return render(
        request,
        "admin_panel/sales_report.html",
        {
            "total_revenue": total_revenue,
            "total_orders": total_orders,
            "avg_order_value": avg_order_value,
            "refunds_total": refunds_total,
            "revenue_change": revenue_change,
            "orders_change": orders_change,
            "aov_change": aov_change,
            "refunds_change": refunds_change,
            "time_series_labels": json.dumps(time_series_labels),
            "time_series_revenue": json.dumps(time_series_revenue),
            "time_series_orders": json.dumps(time_series_orders),
            "category_labels": json.dumps(category_labels),
            "category_revenue": json.dumps(category_revenue),
            "payment_labels": json.dumps(payment_labels),
            "payment_revenue": json.dumps(payment_revenue),
            "product_page": product_page,
            "product_rows": product_rows,
            "categories": Category.objects.all().order_by("name"),
            "category_id": category_id,
            "payment_method": payment_method,
            "date_from": date_from,
            "date_to": date_to,
            "range_filter": range_filter,
        },
    )


def _export_analytics_csv(
    *,
    date_from,
    date_to,
    category_id,
    payment_method,
    total_revenue,
    total_orders,
    avg_order_value,
    refunds_total,
    category_labels,
    category_revenue,
    payment_labels,
    payment_revenue,
    product_rows_qs,
):
    """Build the CSV export response for the sales report."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="sales_report_{date_from}_to_{date_to}.csv"'
    )

    writer = csv.writer(response)

    # Report header
    writer.writerow(["Sales Report"])
    writer.writerow(["Date Range", f"{date_from} to {date_to}"])
    writer.writerow(["Generated At", timezone.now().strftime("%d %b %Y, %I:%M %p")])
    if category_id:
        cat_obj = Category.objects.filter(id=category_id).first()
        writer.writerow(["Category Filter", cat_obj.name if cat_obj else category_id])
    if payment_method:
        writer.writerow(
            ["Payment Method Filter", PAYMENT_METHOD_LABELS.get(payment_method, payment_method)]
        )
    writer.writerow([])

    # Summary
    writer.writerow(["Summary"])
    writer.writerow(["Total Revenue", f"{total_revenue}"])
    writer.writerow(["Total Orders", total_orders])
    writer.writerow(["Average Order Value", f"{avg_order_value}"])
    writer.writerow(["Refunds/Returns", f"{refunds_total}"])
    writer.writerow([])

    # Revenue by category
    writer.writerow(["Revenue by Category"])
    writer.writerow(["Category", "Revenue"])
    for name, rev in zip(category_labels, category_revenue):
        writer.writerow([name, rev])
    writer.writerow([])

    # Revenue by payment method
    writer.writerow(["Revenue by Payment Method"])
    writer.writerow(["Payment Method", "Revenue"])
    for name, rev in zip(payment_labels, payment_revenue):
        writer.writerow([name, rev])
    writer.writerow([])

    # Revenue by product (full, unpaginated)
    writer.writerow(["Revenue by Product"])
    writer.writerow(["Product", "Category", "Units Sold", "Revenue"])
    for row in product_rows_qs:
        writer.writerow(
            [
                row["product__name"],
                row["product__subcategory__category__name"] or "Uncategorized",
                row["units_sold"],
                row["revenue"],
            ]
        )

    return response