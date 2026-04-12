from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from django.contrib.auth.hashers import check_password
import random
from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta
from .models import Address
# Create your views here.

User = get_user_model()

@never_cache
def user_landing_dashboard(request):
    if request.user.is_authenticated:
        return redirect('user_loggedin_dashboard')
    
    return render(request,'user_panel/landing_dashboard.html')

def user_loggedin_dashboard(request):
    return render(request, 'user_panel/loggedin_dashboard.html')

@never_cache
def user_login(request):
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect('admin_dashboard')
        else:
            return redirect('user_loggedin_dashboard')
        
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')

        user = authenticate(request, email=email, password=password)

        if user:
            if user.is_staff:
                messages.error(request, "Invalid Credentials")
                return redirect('user_login')

            if user and user.is_active:
                login(request, user)
                return redirect('user_loggedin_dashboard')
            elif user:
                messages.error(request, "User is Blocked")

        else:
            messages.error(request, "Invalid Credentials")
            return redirect('user_login')

    return render(request, 'user_panel/login.html') 

def user_logout(request):
    logout(request)
    return redirect("user_login")

def user_forgot_password(request):
    if request.method == "POST":
        email = request.POST.get('email')

        try:
            user = User.objects.get(email=email)

            # generate OTP
            otp = str(random.randint(100000, 999999))

            # save OTP
            user.otp = otp
            user.otp_created_at = timezone.now()
            user.save()

            # send email
            send_mail(
                'Your OTP Code',
                f'Your OTP is {otp}',
                'your_email@gmail.com',
                [email],
                fail_silently=False,
            )

            # store email in session
            request.session['reset_email'] = email

            return redirect('user_verify_otp')

        except User.DoesNotExist:
            messages.error(request, "Email not registered")

    return render(request, 'user_panel/forgot_password.html')

def user_verify_otp(request):
    email = request.session.get('reset_email')

    if not email:
        return redirect('user_login')

    user = User.objects.get(email=email)

    if request.method == "POST":
        otp = (
            request.POST.get('otp1', '') +
            request.POST.get('otp2', '') +
            request.POST.get('otp3', '') +
            request.POST.get('otp4', '') +
            request.POST.get('otp5', '') +
            request.POST.get('otp6', '')
        )

        print("DB OTP:", user.otp)
        print("Entered OTP:", otp)

        if user.otp == otp:

            if timezone.now() - user.otp_created_at < timedelta(minutes=5):

                user.otp = None
                user.save()

                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                return redirect('user_loggedin_dashboard')

            else:
                messages.error(request, "OTP expired")

        else:
            messages.error(request, "Invalid OTP")

    return render(request, 'user_panel/verify_otp.html')

def user_signup(request):
    if request.method == "POST":
        name = request.POST.get("name").strip()
        email = request.POST.get("email").strip().lower()
        password1 = request.POST.get("password1").strip()
        password2 = request.POST.get("password2").strip()
        referralcode = request.POST.get("referralcode", "").strip()
        terms = request.POST.get("terms")

        if not name or not email or not password1 or not password2:
            messages.error(request, "All fields are required!")
        
        elif password1 != password2:
            messages.error(request, "Both Password Should match!")

        if not terms:
            messages.error(request, "You must accept terms")
            return redirect('signup')

        elif User.objects.filter(email=email).exists():
            messages.error(request, "Email already registered!")

        else:
            User.objects.create_user(
                fullname=name, email=email, password=password1
            )
            return redirect('user_loggedin_dashboard')

    return render(request, 'user_panel/signup.html')


def user_profile(request):
    user = request.user
    addresses = request.user.addresses.all()

    return render(request, 'user_panel/profile.html', {'user':user, 'addresses':addresses})


def user_add_address(request):
    if request.method == "POST":
        Address.objects.create(
            user=request.user,
            name=request.POST.get('name'),
            phone=request.POST.get('phone'),
            line1=request.POST.get('line1'),
            city=request.POST.get('city'),
            postal_code=request.POST.get('postal_code'),
            country=request.POST.get('country'),
        )
    return redirect('user_profile')


def user_delete_address(request, id):
    address = Address.objects.get(id=id, user=request.user)
    address.delete()
    return redirect('user_profile')


def user_edit_profile(request):
    user = request.user

    if request.method == "POST":
        user.fullname = request.POST.get('fullname')
        user.phone = request.POST.get('phone')
        
        if request.FILES.get('image'):
            user.image = request.FILES.get('image')


        current = request.POST.get('current_password')
        new = request.POST.get('new_password')
        confirm = request.POST.get('confirm_password')

        # only change password if user filled it
        if current and new and confirm:
            if not check_password(current, user.password):
                messages.error(request, "Wrong current password")
                return redirect('user_edit_profile')

            if new != confirm:
                messages.error(request, "Passwords do not match")
                return redirect('user_edit_profile')

            user.set_password(new)

        user.save()
        if current and new and confirm:
            update_session_auth_hash(request, user)

        return redirect('user_profile')
    
    return render(request, 'user_panel/edit_profile.html', {'user': user})

