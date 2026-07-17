import random
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import razorpay
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Prefetch, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from orders.models import Order
from orders.views import calculate_checkout_totals
from products.models import Category, Product, ProductVariant, Wishlist

from .models import Address, Wallet, WalletTransaction

# Create your views here.

User = get_user_model()  # Gets the custom User model


@never_cache
def user_landing_dashboard(request):
    if request.user.is_authenticated:  # If already logged in → skip landing → go dashboard
        return redirect("user_loggedin_dashboard")

    categories = Category.objects.filter(is_blocked=False).order_by("-created_at")

    latest_products = (
        Product.objects.filter(
            is_active=True,
            subcategory__category__is_blocked=False,
        )
        .select_related("subcategory", "subcategory__category")
        .prefetch_related(
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.filter(status="ACTIVE")
                .prefetch_related("images")
                .order_by("created_at"),  # match product_list ordering
                to_attr="active_variants",
            )
        )
        .annotate(total_stock=Sum("variants__stock"))
        .filter(total_stock__gt=0)
        .order_by("-created_at")[:4]
    )

    for product in latest_products:
        active_variants = product.active_variants

        default_variant = None
        for variant in active_variants:
            if variant.is_default:
                default_variant = variant
                break

        if not default_variant and active_variants:
            default_variant = active_variants[0]

        product.display_variant = default_variant

        if default_variant:
            first_image = default_variant.images.first()
            product.thumbnail = first_image.image.url if first_image else None
            product.display_price = default_variant.discounted_price
        else:
            product.thumbnail = None
            product.display_price = product.base_price

    return render(
        request,
        "user_panel/landing_dashboard.html",
        {"categories": categories, "latest_products": latest_products},
    )


@login_required(login_url="user_login")
def user_loggedin_dashboard(request):
    categories = Category.objects.filter(is_blocked=False).order_by("-created_at")

    latest_products = (
        Product.objects.filter(
            is_active=True,
            subcategory__category__is_blocked=False,
        )
        .select_related("subcategory", "subcategory__category")
        .prefetch_related(
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.filter(status="ACTIVE")
                .prefetch_related("images")
                .order_by("created_at"),  # match product_list ordering
                to_attr="active_variants",
            )
        )
        .annotate(total_stock=Sum("variants__stock"))
        .filter(total_stock__gt=0)
        .order_by("-created_at")[:4]
    )

    for product in latest_products:
        active_variants = product.active_variants

        default_variant = None
        for variant in active_variants:
            if variant.is_default:
                default_variant = variant
                break

        if not default_variant and active_variants:
            default_variant = active_variants[0]

        product.display_variant = default_variant

        if default_variant:
            first_image = default_variant.images.first()
            product.thumbnail = first_image.image.url if first_image else None
            product.display_price = default_variant.discounted_price
        else:
            product.thumbnail = None
            product.display_price = product.base_price

    return render(
        request,
        "user_panel/loggedin_dashboard.html",
        {"categories": categories, "latest_products": latest_products},
    )


@never_cache
def user_login(request):
    if request.user.is_authenticated:  # If already loggedin
        if request.user.is_staff:
            return redirect("admin_dashboard")  # Admin → admin dashboard
        else:
            return redirect("user_loggedin_dashboard")  # User → user dashboard

    # Form Submission
    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")

        # Authentication
        user = authenticate(request, email=email, password=password)

        if user is not None:
            if user.is_staff or user.is_superuser:  # Admin trying user login → blocked
                messages.error(request, "Invalid Credentials")
                return redirect("user_login")

            if not user.is_active:  # Inactive user → blocked
                messages.error(request, "User is Inactive")
                return redirect("user_login")

            login(
                request, user
            )  # Creates session, Stores user ID in session, Sends session cookie to browser
            return redirect("user_loggedin_dashboard")

        else:
            messages.error(request, "Invalid Credentials")
            return render(request, "user_panel/login.html", {"form_data": request.POST})

    return render(request, "user_panel/login.html")


@login_required(login_url="user_login")
def user_logout(request):
    logout(request)  # Clears session data, Deletes session cookie, User becomes anonymous
    return redirect("user_login")


def user_forgot_password(request):
    if request.method == "POST":  # If user enters email
        email = request.POST.get("email")  # Get email from form

        try:
            user = User.objects.get(email=email)  # Check if user exists

            # generate OTP
            otp = str(random.randint(100000, 999999))

            user.otp = otp  # Stores OTP in user table
            user.otp_created_at = timezone.now()  # Stores current time
            user.save()

            subject = "FASHIO - Verify Your Email"

            message = f"""
            Hi,

            Welcome to FASHIO 

            To continue, please use the OTP below to verify your email:

            🔐 OTP: {otp}

            This OTP is valid for 5 minutes.

            If you didn’t request this, please ignore this email.

            Thanks,
            Team FASHIO
            Your Style, Your Identity
            """
            # send email
            send_mail(
                subject,
                message,
                settings.EMAIL_HOST_USER,
                [email],
                fail_silently=False,
            )

            # store email in session
            request.session["reset_email"] = email

            return redirect("user_verify_otp")

        except User.DoesNotExist:
            messages.error(request, "Email not registered")

    return render(request, "user_panel/forgot_password.html")


@never_cache
def user_verify_otp(request):
    email = request.session.get("reset_email")  # Get email from session

    # If email not found
    if not email:
        return redirect("user_login")

    try:
        user = User.objects.get(email=email)  # Get user from database
    except User.DoesNotExist:
        messages.error(request, "User not found")
        return redirect("user_login")

    remaining_seconds = 0

    if user.otp_created_at:
        diff = (timezone.now() - user.otp_created_at).total_seconds()
        if diff < 60:
            remaining_seconds = int(60 - diff)
        else:
            remaining_seconds = 0

    if request.method == "POST":  # If user submits OTP
        otp = (
            request.POST.get("otp1", "")
            + request.POST.get("otp2", "")
            + request.POST.get("otp3", "")
            + request.POST.get("otp4", "")
            + request.POST.get("otp5", "")
            + request.POST.get("otp6", "")
        )

        # Limit OTP attempts
        if user.otp_attempts >= 5:
            user.otp = None
            user.save()
            messages.error(request, "Too many attempts. Try again later.")
            return redirect("user_verify_otp")

        # Check if OTP matches (OTP exists, OTP matches)
        if user.otp and user.otp == otp:

            # Check OTP expiry (5 minutes)
            if timezone.now() - user.otp_created_at < timedelta(minutes=5):

                # clear OTP after use
                user.otp = None
                user.otp_attempts = 0
                user.save()

                # removes session after success
                if "reset_email" in request.session:
                    del request.session["reset_email"]

                login(
                    request, user, backend="django.contrib.auth.backends.ModelBackend"
                )  # Logs user in directly after OTP verification
                return redirect("user_loggedin_dashboard")

            else:
                user.otp = None
                user.save()
                messages.error(request, "OTP expired")
                return redirect("user_verify_otp")

        else:  # If OTP is wrong
            user.otp_attempts += 1
            user.save()

            remaining = max(0, 5 - user.otp_attempts)
            messages.error(request, f"Invalid OTP. {remaining} attempts left.")

    return render(request, "user_panel/verify_otp.html", {"remaining_seconds": remaining_seconds})


def user_change_email(request):
    if (
        "temp_user_data" in request.session
    ):  # Check signup session, if this exists user is in signup flow
        return redirect("user_signup")

    if (
        "reset_email" in request.session
    ):  # Check forgot password session, if this exists user is in forgot password flow
        del request.session[
            "reset_email"
        ]  # Remove old email, User wants to change email, Old email should not be reused
        return redirect("user_forgot_password")

    if "email_edit_new_email" in request.session:
        del request.session["email_edit_new_email"]
        del request.session["email_edit_otp"]
        del request.session["email_edit_otp_created_at"]
        return redirect("user_edit_profile")

    return redirect("user_login")


def user_resend_otp(request):  # Handles 2 different cases
    email = None
    is_signup = False
    is_email_edit = False

    # 1. Identify which flow user is in
    if "temp_user_data" in request.session:  # Means user is signing up
        email = request.session["temp_user_data"]["email"]  # Get email from session
        is_signup = True  # Mark this as signup flow

    elif "reset_email" in request.session:  # Means user is resetting password
        email = request.session["reset_email"]

    elif "email_edit_new_email" in request.session:
        email = request.session["email_edit_new_email"]
        is_email_edit = True

    # If no session data found
    if not email:
        return redirect("user_login")

    # 2. Logic for Signup Resend (Session-based)
    if is_signup:
        # Check cooldown from session
        last_sent = request.session.get("otp_created_at")
        if last_sent:

            last_sent_time = parse_datetime(
                last_sent
            )  # Convert string → datetime because Session stores it as string
            diff = (timezone.now() - last_sent_time).total_seconds()  # Calculate time difference

            if diff < 60:
                messages.error(request, f"Please wait {int(60 - diff)}s before resending")
                return redirect("user_verify_signup_otp")

        otp = str(random.randint(100000, 999999))  # Generate new OTP
        # Store in session
        request.session["otp"] = otp
        request.session["otp_created_at"] = str(timezone.now())

        subject = "FASHIO - Verify Your Email"

        message = f"""
        Hi,

        Welcome to FASHIO 

        To continue, please use the OTP below to verify your email:

        🔐 OTP: {otp}

        This OTP is valid for 5 minutes.

        If you didn’t request this, please ignore this email.

        Thanks,
        Team FASHIO
        Your Style, Your Identity
        """

        send_mail(subject, message, settings.EMAIL_HOST_USER, [email])  # Send email
        messages.success(request, "New OTP sent successfully")
        return redirect("user_verify_signup_otp")

    elif is_email_edit:
        otp_created_at_str = request.session.get("email_edit_otp_created_at")
        if otp_created_at_str:
            otp_created_at = timezone.datetime.fromisoformat(otp_created_at_str)
            diff = (timezone.now() - otp_created_at).total_seconds()
            if diff < 60:
                messages.error(request, f"Please wait {int(60 - diff)}s before resending")
                return redirect("user_verify_email_edit_otp")

        otp = str(random.randint(100000, 999999))
        request.session["email_edit_otp"] = otp
        request.session["email_edit_otp_created_at"] = str(timezone.now())

        subject = "FASHIO - Verify Your New Email"
        message = f"""
        Hi,

        Your new OTP for email change:

        🔐 OTP: {otp}

        This OTP is valid for 5 minutes.

        Thanks,
        Team FASHIO
        Your Style, Your Identity
        """
        send_mail(subject, message, settings.EMAIL_HOST_USER, [email])
        messages.success(request, "New OTP sent successfully")
        return redirect("user_verify_email_edit_otp")

    # 3. Logic for Forgot Password Resend (DB-based)
    else:
        try:
            user = User.objects.get(email=email)  # Get user
        except User.DoesNotExist:
            messages.error(request, "User not found. Please try again.")
            return redirect("user_login")

        # Cooldown check
        if user.otp_created_at:
            diff = (timezone.now() - user.otp_created_at).total_seconds()

            if diff < 60:
                messages.error(request, f"Please wait {int(60 - diff)}s before resending")
                return redirect("user_verify_otp")

        # Generate new OTP
        otp = str(random.randint(100000, 999999))

        # Save to database
        user.otp = otp
        user.otp_created_at = timezone.now()
        user.otp_attempts = 0
        user.save()

        subject = "FASHIO - Verify Your Email"

        message = f"""
        Hi,

        Welcome to FASHIO 

        To continue, please use the OTP below to verify your email:

        🔐 OTP: {otp}

        This OTP is valid for 5 minutes.

        If you didn’t request this, please ignore this email.

        Thanks,
        Team FASHIO
        Your Style, Your Identity
        """

        send_mail(subject, message, settings.EMAIL_HOST_USER, [email])
        messages.success(request, "New OTP sent successfully")
        return redirect("user_verify_otp")


@never_cache
def user_signup(request):

    # If user is already loggedin
    if request.user.is_authenticated:
        return redirect("user_loggedin_dashboard")

    # Form submission
    if request.method == "POST":
        name = request.POST.get("name").strip()
        email = request.POST.get("email").strip().lower()
        password1 = request.POST.get("password1").strip()
        password2 = request.POST.get("password2").strip()
        referralcode = request.POST.get("referral_code", "").strip().upper()
        terms = request.POST.get("terms")

        referrer = None

        # Name validation
        if not name:
            messages.error(request, "Name is required")
        elif len(name) < 3:
            messages.error(request, "Name must be at least 3 characters")
        elif not re.match(r"^[A-Za-z ]+$", name):
            messages.error(request, "Name should contain only letters")

        # Email validation
        elif not email:
            messages.error(request, "Email is required")
        elif User.objects.filter(email=email).exists():
            messages.error(request, "Email already exists")

        # Password validation
        elif not password1:
            messages.error(request, "Password is required")
        elif len(password1) < 6:
            messages.error(request, "Password must be at least 6 characters")
        elif not re.search(r"[A-Z]", password1):
            messages.error(request, "Password must contain 1 uppercase letter")
        elif not re.search(r"[0-9]", password1):
            messages.error(request, "Password must contain 1 number")

        # Confirm password
        elif password1 != password2:
            messages.error(request, "Passwords do not match")

        # Terms
        elif not terms:
            messages.error(request, "You must accept terms")

        # Referral code validation (optional field)
        elif referralcode and not User.objects.filter(referral_code=referralcode).exists():
            messages.error(request, "Invalid referral code")

        # Store data in session
        else:
            if referralcode:
                referrer = User.objects.filter(referral_code=referralcode).first()

            request.session["temp_user_data"] = {
                "name": name,
                "email": email,
                "password": password1,
                "referral_code": referralcode if referrer else None,
            }

            # Generate OTP
            otp = str(random.randint(100000, 999999))

            # Stores in session
            request.session["otp"] = otp
            request.session["otp_created_at"] = str(timezone.now())

            # Send email
            subject = "FASHIO - Verify Your Email"

            message = f"""
            Hi,

            Welcome to FASHIO 

            To continue, please use the OTP below to verify your email:

            🔐 OTP: {otp}

            This OTP is valid for 5 minutes.

            If you didn’t request this, please ignore this email.

            Thanks,
            Team FASHIO
            Your Style, Your Identity
            """

            send_mail(subject, message, settings.EMAIL_HOST_USER, [email])

            return redirect("user_verify_signup_otp")

    return render(request, "user_panel/signup.html", {"temp_data": request.POST})


@never_cache
def user_verify_signup_otp(request):

    # if user already logged in
    if request.user.is_authenticated:
        return redirect("user_loggedin_dashboard")

    # Check session data exists
    if "temp_user_data" not in request.session:
        return redirect("user_signup")

    # Initialize timer
    remaining_seconds = 0
    otp_created_at_str = request.session.get(
        "otp_created_at"
    )  # Get OTP creation time from session

    if otp_created_at_str:
        # Convert the stored string back into a datetime
        otp_created_at = timezone.datetime.fromisoformat(otp_created_at_str)
        diff = (timezone.now() - otp_created_at).total_seconds()
        if diff < 60:
            remaining_seconds = int(60 - diff)

    if request.method == "POST":
        user_otp = (
            request.POST.get("otp1", "")
            + request.POST.get("otp2", "")
            + request.POST.get("otp3", "")
            + request.POST.get("otp4", "")
            + request.POST.get("otp5", "")
            + request.POST.get("otp6", "")
        )

        # Get stores OTP from the session
        stored_otp = request.session.get("otp")

        # If OTP is correct
        if user_otp == stored_otp:
            # OTP Correct: Create the user now
            temp_data = request.session.get("temp_user_data")

            user = User.objects.create_user(
                fullname=temp_data["name"],
                email=temp_data["email"],
                password=temp_data["password"],
            )

            # Link referrer if a valid referral code was provided at signup
            ref_code = temp_data.get("referral_code")
            if ref_code:
                referrer = User.objects.filter(referral_code=ref_code).exclude(pk=user.pk).first()
                if referrer:
                    user.referred_by = referrer
                    user.save(update_fields=["referred_by"])

            login(
                request, user, backend="django.contrib.auth.backends.ModelBackend"
            )  # Logs user in directly after OTP verification

            # Clear session data
            del request.session["temp_user_data"]
            del request.session["otp"]
            del request.session["otp_created_at"]

            return redirect("user_loggedin_dashboard")
        else:
            messages.error(request, "Invalid OTP")

    return render(request, "user_panel/verify_otp.html", {"remaining_seconds": remaining_seconds})


@login_required(login_url="user_login")
def user_profile(request):
    user = request.user
    addresses = request.user.addresses.all()
    latest_address = addresses.order_by("-created_at").first()

    orders = (
        Order.objects.filter(user=request.user)
        .prefetch_related("items", "items__variant")
        .order_by("-created_at")[:5]
    )

    for order in orders:
        billable_items = order.items.filter(item_status__in=["ACTIVE", "RETURN_REQUESTED"])

        active_items = order.items.filter(item_status="ACTIVE")
        cancelled_items = order.items.filter(item_status="CANCELLED")
        return_requested_items = order.items.filter(item_status="RETURN_REQUESTED")
        returned_items = order.items.filter(item_status="RETURNED")

        order.has_active_items = active_items.exists()

        order.billable_subtotal = sum(item.subtotal for item in billable_items)
        order.cancelled_subtotal = sum(item.subtotal for item in cancelled_items)
        order.returned_subtotal = sum(item.subtotal for item in returned_items)

        order.has_cancelled_items = cancelled_items.exists()
        order.has_return_requested_items = return_requested_items.exists()
        order.has_returned_items = returned_items.exists()

        if order.billable_subtotal > 0:
            totals = calculate_checkout_totals(order.billable_subtotal)
            order.display_delivery_fee = totals["delivery_fee"]
            order.display_gst_amount = totals["gst_amount"]
            order.display_gst_rate = totals["gst_rate"]
            order.display_total_amount = totals["total_payable"]
        else:
            order.display_delivery_fee = Decimal("0.00")
            order.display_gst_amount = Decimal("0.00")
            order.display_total_amount = Decimal("0.00")

    wishlist_items = (
        Wishlist.objects.filter(user=request.user)
        .select_related("product", "variant")
        .prefetch_related("variant__images")
        .order_by("-created_at")
    )

    for item in wishlist_items:
        first_image = item.variant.images.first()
        item.thumbnail = first_image.image.url if first_image else None

        item.display_price = item.variant.price

        if item.variant.discount > 0:
            item.display_price = item.variant.price - (
                item.variant.price * item.variant.discount / 100
            )

    open_modal = request.session.pop("open_modal", None)

    return render(
        request,
        "user_panel/profile.html",
        {
            "user": user,
            "addresses": addresses,
            "latest_address": latest_address,
            "open_modal": open_modal,
            "orders": orders,
            "wishlist_items": wishlist_items,
        },
    )


@login_required(login_url="user_login")
def user_add_address(request):
    if request.method == "POST":
        next_url = request.POST.get("next")

        name = request.POST.get("name", "").strip()
        phone = request.POST.get("phone", "").strip()
        line1 = request.POST.get("line1", "").strip()
        city = request.POST.get("city", "").strip()
        postal_code = request.POST.get("postal_code", "").strip()
        state = request.POST.get("state", "").strip()

        error = None

        if not re.match(r"^[A-Za-z ]{3,}$", name):
            error = "Enter valid name (min 3 letters)"
        elif not re.match(r"^\d{10}$", phone):
            error = "Enter valid 10-digit phone number"
        elif len(line1) < 5:
            error = "Address must be at least 5 characters"
        elif not re.match(r"^[A-Za-z ]+$", city):
            error = "Enter valid city"
        elif not re.match(r"^\d{6}$", postal_code):
            error = "Enter valid postal code"
        elif not state:
            error = "Please select a state"

        if error:
            messages.error(request, error)
            addresses = request.user.addresses.all()
            return render(
                request,
                "user_panel/profile.html",
                {
                    "user": request.user,
                    "addresses": addresses,
                    "latest_address": addresses.order_by("-created_at").first(),  # ← add this
                    "open_modal": "new-address",
                    "new_address_data": request.POST,
                },
            )

        Address.objects.create(
            user=request.user,
            name=name,
            phone=phone,
            line1=line1,
            city=city,
            postal_code=postal_code,
            state=state,
        )

        if next_url:
            return redirect(next_url)

        return redirect("user_profile")

    return redirect("user_profile")


@login_required(login_url="user_login")
def user_edit_address(request, id):
    address = get_object_or_404(Address, id=id, user=request.user)

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        phone = request.POST.get("phone", "").strip()
        line1 = request.POST.get("line1", "").strip()
        city = request.POST.get("city", "").strip()
        postal_code = request.POST.get("postal_code", "").strip()
        state = request.POST.get("state", "").strip()

        error = None

        if not re.match(r"^[A-Za-z ]{3,}$", name):
            error = "Enter valid name (min 3 letters)"
        elif not re.match(r"^\d{10}$", phone):
            error = "Enter valid 10-digit phone number"
        elif len(line1) < 5:
            error = "Address must be at least 5 characters"
        elif not re.match(r"^[A-Za-z ]+$", city):
            error = "Enter valid city"
        elif not re.match(r"^\d{6}$", postal_code):
            error = "Enter valid postal code"
        elif not state:
            error = "Please select a state"

        if error:
            messages.error(request, error)
            addresses = request.user.addresses.all()
            return render(
                request,
                "user_panel/profile.html",
                {
                    "user": request.user,
                    "addresses": addresses,
                    "latest_address": addresses.order_by("-created_at").first(),  # ← add this
                    "open_modal": "edit-address",
                    "edit_address_data": request.POST,
                    "edit_address_id": id,
                },
            )

        address.name = name
        address.phone = phone
        address.line1 = line1
        address.city = city
        address.postal_code = postal_code
        address.state = state
        address.save()

        return redirect(request.POST.get("next") or "user_profile")

    return redirect("user_profile")


@login_required(login_url="user_login")
def user_delete_address(request, id):

    address = Address.objects.get(id=id, user=request.user)
    address.delete()
    return redirect("user_profile")


@login_required(login_url="user_login")
def user_edit_profile(request):

    user = request.user
    email_error = request.session.pop("email_edit_error", None)
    show_email_field = email_error is not None

    if request.method == "POST":
        fullname = request.POST.get("fullname", "").strip()
        phone = request.POST.get("phone", "").strip()

        # Fullname validation
        if not fullname:
            messages.error(request, "Full name is required")
            return render(request, "user_panel/edit_profile.html", {"user": user})
        elif len(fullname) < 3:
            messages.error(request, "Full name must be at least 3 characters")
            return render(request, "user_panel/edit_profile.html", {"user": user})
        elif not re.match(r"^[A-Za-z ]+$", fullname):
            messages.error(request, "Full name should contain only letters")
            return render(request, "user_panel/edit_profile.html", {"user": user})

        # Phone validation
        if not re.match(r"^\d{10}$", phone):
            messages.error(request, "Enter a valid 10-digit phone number")
            return render(request, "user_panel/edit_profile.html", {"user": user})

        # Profile image validation
        if request.FILES.get("image"):
            image = request.FILES["image"]
            allowed_types = ["image/jpeg", "image/png", "image/webp"]
            if image.content_type not in allowed_types:
                messages.error(request, "Only JPG, PNG, or WEBP images allowed")
                return render(request, "user_panel/edit_profile.html", {"user": user})
            if image.size > 2 * 1024 * 1024:
                messages.error(request, "Image must be under 2MB")
                return render(request, "user_panel/edit_profile.html", {"user": user})
            user.image = image

        # Phone validation
        if not re.match(r"^\d{10}$", phone):
            messages.error(request, "Enter a valid 10-digit phone number")
            return render(request, "user_panel/edit_profile.html", {"user": user})

        # Phone duplicate check
        if User.objects.filter(phone=phone).exclude(pk=user.pk).exists():
            messages.error(request, "This phone number is already in use")
            return render(request, "user_panel/edit_profile.html", {"user": user})

        # Password — only for email users
        if user.auth_provider == "email":
            current = request.POST.get("current_password", "").strip()
            new = request.POST.get("new_password", "").strip()
            confirm = request.POST.get("confirm_password", "").strip()

            if current or new or confirm:
                if not current:
                    messages.error(request, "Enter your current password")
                    return render(request, "user_panel/edit_profile.html", {"user": user})
                if not check_password(current, user.password):
                    messages.error(request, "Current password is incorrect")
                    return render(request, "user_panel/edit_profile.html", {"user": user})
                if not new:
                    messages.error(request, "Enter a new password")
                    return render(request, "user_panel/edit_profile.html", {"user": user})
                if len(new) < 6:
                    messages.error(request, "New password must be at least 6 characters")
                    return render(request, "user_panel/edit_profile.html", {"user": user})
                if not re.search(r"[A-Z]", new):
                    messages.error(
                        request, "New password must contain at least 1 uppercase letter"
                    )
                    return render(request, "user_panel/edit_profile.html", {"user": user})
                if not re.search(r"[0-9]", new):
                    messages.error(request, "New password must contain at least 1 number")
                    return render(request, "user_panel/edit_profile.html", {"user": user})
                if new != confirm:
                    messages.error(request, "Passwords do not match")
                    return render(request, "user_panel/edit_profile.html", {"user": user})

                user.set_password(new)

        user.fullname = fullname
        user.phone = phone
        user.save()

        if user.auth_provider == "email":
            update_session_auth_hash(request, user)

        messages.success(request, "Profile updated successfully")
        return redirect("user_profile")

    return render(
        request,
        "user_panel/edit_profile.html",
        {
            "user": user,
            "show_email_field": show_email_field,
            "email_error": email_error,
        },
    )


@login_required(login_url="user_login")
def user_edit_email(request):
    """Handles the email change request from edit profile form"""
    user = request.user

    if request.method == "POST":
        new_email = request.POST.get("new_email", "").strip().lower()

        # Validation
        if not new_email:
            request.session["email_edit_error"] = "Email is required"
            return redirect("user_edit_profile")
        if not re.match(r"^[^@]+@[^@]+\.[^@]+$", new_email):
            request.session["email_edit_error"] = "Email is required"
            return redirect("user_edit_profile")

        if new_email == user.email:
            request.session["email_edit_error"] = "New email is same as current email"
            return redirect("user_edit_profile")

        if User.objects.filter(email=new_email).exists():
            request.session["email_edit_error"] = "Email already in use"
            return redirect("user_edit_profile")

        # Generate OTP
        otp = str(random.randint(100000, 999999))

        # Store in session
        request.session["email_edit_new_email"] = new_email
        request.session["email_edit_otp"] = otp
        request.session["email_edit_otp_created_at"] = str(timezone.now())

        # Send OTP to NEW email
        subject = "FASHIO - Verify Your New Email"
        message = f"""
        Hi,

        You requested an email change on FASHIO.

        Please use the OTP below to verify your new email:

        🔐 OTP: {otp}

        This OTP is valid for 5 minutes.

        If you didn't request this, please ignore this email.

        Thanks,
        Team FASHIO
        Your Style, Your Identity
        """
        send_mail(subject, message, settings.EMAIL_HOST_USER, [new_email], fail_silently=False)

        return redirect("user_verify_email_edit_otp")

    return redirect("user_edit_profile")


@login_required(login_url="user_login")
@never_cache
def user_verify_email_edit_otp(request):
    """Verifies OTP and updates the email"""

    # Check session exists
    new_email = request.session.get("email_edit_new_email")
    if not new_email:
        return redirect("user_edit_profile")

    # Timer
    remaining_seconds = 0
    otp_created_at_str = request.session.get("email_edit_otp_created_at")
    if otp_created_at_str:
        otp_created_at = timezone.datetime.fromisoformat(otp_created_at_str)
        diff = (timezone.now() - otp_created_at).total_seconds()
        if diff < 60:
            remaining_seconds = int(60 - diff)

    if request.method == "POST":
        user_otp = (
            request.POST.get("otp1", "")
            + request.POST.get("otp2", "")
            + request.POST.get("otp3", "")
            + request.POST.get("otp4", "")
            + request.POST.get("otp5", "")
            + request.POST.get("otp6", "")
        )

        stored_otp = request.session.get("email_edit_otp")
        otp_created_at_str = request.session.get("email_edit_otp_created_at")
        otp_created_at = timezone.datetime.fromisoformat(otp_created_at_str)

        # Check expiry
        if timezone.now() - otp_created_at >= timedelta(minutes=5):
            messages.error(request, "OTP expired. Please try again.")
            # Clear session
            del request.session["email_edit_new_email"]
            del request.session["email_edit_otp"]
            del request.session["email_edit_otp_created_at"]
            return redirect("user_edit_profile")

        if user_otp == stored_otp:
            # Update email
            user = request.user
            user.email = new_email
            user.save(update_fields=["email"])

            # Clear session
            del request.session["email_edit_new_email"]
            del request.session["email_edit_otp"]
            del request.session["email_edit_otp_created_at"]

            # Keep user logged in
            update_session_auth_hash(request, user)

            messages.success(request, "Email updated successfully")
            return redirect("user_profile")

        else:
            messages.error(request, "Invalid OTP. Please try again.")

    return render(
        request,
        "user_panel/verify_otp.html",
        {
            "remaining_seconds": remaining_seconds,
            "new_email": new_email,
        },
    )


@login_required
def user_wallet(request):
    wallet, _ = Wallet.objects.get_or_create(user=request.user)

    transactions = wallet.transactions.all()

    paginator = Paginator(transactions, 5)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    total_added = wallet.transactions.filter(transaction_type="CREDIT").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    total_spent = wallet.transactions.filter(transaction_type="DEBIT").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    context = {
        "wallet": wallet,
        "transactions": transactions,
        "page_obj": page_obj,
        "total_added": total_added,
        "total_spent": total_spent,
        "open_razorpay": False,
    }

    return render(request, "user_panel/wallet.html", context)


@login_required(login_url="user_login")
def user_add_wallet_money(request):
    if request.method != "POST":
        return redirect("user_wallet")

    amount = request.POST.get("amount", "").strip()

    try:
        amount = Decimal(amount)
    except InvalidOperation:
        messages.error(request, "Please enter a valid amount.")
        return redirect("user_wallet")

    if amount <= 0:
        messages.error(request, "Amount must be greater than zero.")
        return redirect("user_wallet")

    if amount > Decimal("50000"):
        messages.error(request, "Maximum amount is ₹50,000.")
        return redirect("user_wallet")

    amount_in_paise = int((amount * 100).quantize(Decimal("1")))

    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    try:
        razorpay_order = client.order.create(
            {
                "amount": amount_in_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "purpose": "WALLET_RECHARGE",
                    "user_id": str(request.user.id),
                    "amount": str(amount),
                },
            }
        )
    except Exception:
        messages.error(request, "Unable to start payment. Please try again.")
        return redirect("user_wallet")

    # Session kept as a convenience fallback only — not relied on for verification.
    request.session["wallet_recharge_amount"] = str(amount)
    request.session["wallet_razorpay_order_id"] = razorpay_order["id"]
    request.session.modified = True

    wallet, created = Wallet.objects.get_or_create(user=request.user)
    transactions = wallet.transactions.all()[:10]

    total_added = Decimal("0.00")
    total_spent = Decimal("0.00")

    for txn in wallet.transactions.all():
        if txn.transaction_type == "CREDIT":
            total_added += txn.amount
        elif txn.transaction_type == "DEBIT":
            total_spent += txn.amount

    return render(
        request,
        "user_panel/wallet.html",
        {
            "wallet": wallet,
            "transactions": transactions,
            "total_added": total_added,
            "total_spent": total_spent,
            # Razorpay data
            "open_razorpay": True,
            "razorpay_key_id": settings.RAZORPAY_KEY_ID,
            "razorpay_order_id": razorpay_order["id"],
            "amount_in_paise": amount_in_paise,
            "amount": amount,
        },
    )


@csrf_exempt
@require_POST
def user_wallet_payment_success(request):
    razorpay_payment_id = request.POST.get("razorpay_payment_id")
    razorpay_order_id = request.POST.get("razorpay_order_id")
    razorpay_signature = request.POST.get("razorpay_signature")

    if not razorpay_payment_id or not razorpay_order_id or not razorpay_signature:
        messages.error(request, "Invalid payment response.")
        return redirect("user_wallet")

    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    try:
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
            }
        )
    except razorpay.errors.SignatureVerificationError:
        messages.error(request, "Payment verification failed.")
        return redirect("user_wallet")

    if WalletTransaction.objects.filter(razorpay_payment_id=razorpay_payment_id).exists():
        messages.info(request, "This payment is already added to wallet.")
        return redirect("user_wallet")

    # Pull the source of truth (amount + user) from the order itself,
    # since session continuity across Razorpay's redirect isn't guaranteed.
    try:
        razorpay_order = client.order.fetch(razorpay_order_id)
    except Exception:
        messages.error(request, "Unable to confirm payment. Please contact support.")
        return redirect("user_wallet")

    notes = razorpay_order.get("notes", {})
    user_id = notes.get("user_id")
    amount_str = notes.get("amount")

    if not user_id or not amount_str:
        messages.error(request, "Unable to confirm payment details.")
        return redirect("user_wallet")

    try:
        amount = Decimal(amount_str)
    except InvalidOperation:
        messages.error(request, "Invalid payment amount.")
        return redirect("user_wallet")

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, "Unable to confirm payment user.")
        return redirect("user_wallet")

    wallet, created = Wallet.objects.get_or_create(user=user)

    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])

    WalletTransaction.objects.create(
        wallet=wallet,
        amount=amount,
        transaction_type="CREDIT",
        purpose="RECHARGE",
        razorpay_payment_id=razorpay_payment_id,
        description="Money added to wallet through Razorpay.",
    )

    # Log the user back into their session if it was lost in the redirect,
    # so user_wallet renders as them rather than anonymous.
    if not request.user.is_authenticated:
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    request.session.pop("wallet_recharge_amount", None)
    request.session.pop("wallet_razorpay_order_id", None)
    request.session.modified = True

    messages.success(request, "Money added to wallet successfully.")
    return redirect("user_wallet")