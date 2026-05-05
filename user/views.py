from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required #Protect pages (only logged-in users allowed)
from django.views.decorators.cache import never_cache #Prevent browser caching
from django.contrib.auth.hashers import check_password #Verify password securely
import random #Generate OTP
import re
from django.core.mail import send_mail #Send OTP email
from django.utils import timezone
from datetime import timedelta
from django.utils.dateparse import parse_datetime
from .models import Address
# Create your views here.

User = get_user_model() #Gets the custom User model

@never_cache
def user_landing_dashboard(request):
    if request.user.is_authenticated: #If already logged in → skip landing → go dashboard
        return redirect('user_loggedin_dashboard')
    
    return render(request,'user_panel/landing_dashboard.html')

@login_required(login_url='user_login')
def user_loggedin_dashboard(request):
    return render(request, 'user_panel/loggedin_dashboard.html')

@never_cache
def user_login(request):
    if request.user.is_authenticated: #If already loggedin 
        if request.user.is_staff: 
            return redirect('admin_dashboard') #Admin → admin dashboard
        else:
            return redirect('user_loggedin_dashboard') #User → user dashboard
    
    #Form Submission
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')

        #Authentication
        user = authenticate(request, email=email, password=password)

        if user is not None:
            if user.is_staff or user.is_superuser: #Admin trying user login → blocked
                messages.error(request, "Invalid Credentials")
                return redirect('user_login')

            if not user.is_active: #Inactive user → blocked
                messages.error(request, "User is Inactive")
                return redirect('user_login')
            
            login(request, user) #Creates session, Stores user ID in session, Sends session cookie to browser
            return redirect('user_loggedin_dashboard')

        else:
            messages.error(request, "Invalid Credentials")
            return render(request, 'user_panel/login.html', {
            'form_data': request.POST
            })

    return render(request, 'user_panel/login.html')


@login_required(login_url='user_login')
def user_logout(request):
    logout(request) #Clears session data, Deletes session cookie, User becomes anonymous
    return redirect("user_login")

def user_forgot_password(request):
    if request.method == "POST": #If user enters email
        email = request.POST.get('email') #Get email from form

        try:
            user = User.objects.get(email=email) #Check if user exists

            # generate OTP
            otp = str(random.randint(100000, 999999))

            user.otp = otp #Stores OTP in user table
            user.otp_created_at = timezone.now() #Stores current time
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
                'fashio@gmail.com',
                [email],
                fail_silently=False,
            )

            # store email in session
            request.session['reset_email'] = email

            return redirect('user_verify_otp')

        except User.DoesNotExist:
            messages.error(request, "Email not registered")

    return render(request, 'user_panel/forgot_password.html')

@never_cache
def user_verify_otp(request):
    email = request.session.get('reset_email') #Get email from session

    #If email not found
    if not email:
        return redirect('user_login')

    try:
        user = User.objects.get(email=email) # Get user from database
    except User.DoesNotExist:
        messages.error(request, "User not found")
        return redirect('user_login')

    remaining_seconds = 0

    if user.otp_created_at:
        diff = (timezone.now() - user.otp_created_at).total_seconds()
        if diff < 60:
            remaining_seconds = int(60 - diff)
        else:
            remaining_seconds = 0

    if request.method == "POST": #If user submits OTP
        otp = (
            request.POST.get('otp1', '') +
            request.POST.get('otp2', '') +
            request.POST.get('otp3', '') +
            request.POST.get('otp4', '') +
            request.POST.get('otp5', '') +
            request.POST.get('otp6', '')
        )

        #Limit OTP attempts
        if user.otp_attempts >= 5:
            user.otp = None
            user.save()
            messages.error(request, "Too many attempts. Try again later.")
            return redirect('user_verify_otp')

        #Check if OTP matches (OTP exists, OTP matches)
        if user.otp and user.otp == otp:

            #Check OTP expiry (5 minutes)
            if timezone.now() - user.otp_created_at < timedelta(minutes=5):
                
                #clear OTP after use
                user.otp = None
                user.otp_attempts = 0
                user.save()

                #removes session after success
                if 'reset_email' in request.session:
                    del request.session['reset_email']

                login(request, user, backend='django.contrib.auth.backends.ModelBackend') #Logs user in directly after OTP verification
                return redirect('user_loggedin_dashboard')

            else:
                user.otp = None
                user.save()
                messages.error(request, "OTP expired")
                return redirect('user_verify_otp')

        else:#If OTP is wrong
            user.otp_attempts += 1
            user.save()

            remaining = max(0, 5 - user.otp_attempts)
            messages.error(request, f"Invalid OTP. {remaining} attempts left.")

    return render(request, 'user_panel/verify_otp.html',  {
        'remaining_seconds': remaining_seconds
    })

def user_change_email(request):
    if 'temp_user_data' in request.session: # Check signup session, if this exists user is in signup flow
        return redirect('user_signup')

    if 'reset_email' in request.session: # Check forgot password session, if this exists user is in forgot password flow
        del request.session['reset_email'] # Remove old email, User wants to change email, Old email should not be reused
        return redirect('user_forgot_password')
    
    if 'email_edit_new_email' in request.session:
        del request.session['email_edit_new_email']
        del request.session['email_edit_otp']
        del request.session['email_edit_otp_created_at']
        return redirect('user_edit_profile')

    return redirect('user_login')

def user_resend_otp(request): # Handles 2 different cases
    email = None
    is_signup = False
    is_email_edit = False
    
    # 1. Identify which flow user is in
    if 'temp_user_data' in request.session: # Means user is signing up
        email = request.session['temp_user_data']['email'] # Get email from session
        is_signup = True # Mark this as signup flow

    elif 'reset_email' in request.session: # Means user is resetting password
        email = request.session['reset_email']

    elif 'email_edit_new_email' in request.session:
        email = request.session['email_edit_new_email']
        is_email_edit = True

    # If no session data found
    if not email:
        return redirect('user_login')

    # 2. Logic for Signup Resend (Session-based)
    if is_signup:
        # Check cooldown from session
        last_sent = request.session.get('otp_created_at')
        if last_sent:
            
            last_sent_time = parse_datetime(last_sent)  # Convert string → datetime because Session stores it as string
            diff = (timezone.now() - last_sent_time).total_seconds() # Calculate time difference

            if diff < 60:
                messages.error(request, f"Please wait {int(60 - diff)}s before resending")
                return redirect('user_verify_signup_otp')

        otp = str(random.randint(100000, 999999)) # Generate new OTP
        # Store in session
        request.session['otp'] = otp 
        request.session['otp_created_at'] = str(timezone.now())
        
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

        send_mail(subject, message, 'fashio@gmail.com', [email]) # Send email
        messages.success(request, "New OTP sent successfully")
        return redirect('user_verify_signup_otp')

    elif is_email_edit:
        otp_created_at_str = request.session.get('email_edit_otp_created_at')
        if otp_created_at_str:
            otp_created_at = timezone.datetime.fromisoformat(otp_created_at_str)
            diff = (timezone.now() - otp_created_at).total_seconds()
            if diff < 60:
                messages.error(request, f"Please wait {int(60 - diff)}s before resending")
                return redirect('user_verify_email_edit_otp')

        otp = str(random.randint(100000, 999999))
        request.session['email_edit_otp'] = otp
        request.session['email_edit_otp_created_at'] = str(timezone.now())

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
        send_mail(subject, message, 'fashio@gmail.com', [email])
        messages.success(request, "New OTP sent successfully")
        return redirect('user_verify_email_edit_otp')

    # 3. Logic for Forgot Password Resend (DB-based)
    else:
        try:
            user = User.objects.get(email=email) # Get user
        except User.DoesNotExist:
            messages.error(request, "User not found. Please try again.")
            return redirect('user_login')

        # Cooldown check
        if user.otp_created_at:
            diff = (timezone.now() - user.otp_created_at).total_seconds()

            if diff < 60:
                messages.error(request, f"Please wait {int(60 - diff)}s before resending")
                return redirect('user_verify_otp')

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

        send_mail(subject, message, 'fashio@gmail.com', [email])
        messages.success(request, "New OTP sent successfully")
        return redirect('user_verify_otp')

@never_cache
def user_signup(request):

    # If user is already loggedin
    if request.user.is_authenticated: 
        return redirect('user_loggedin_dashboard')

    # Form submission
    if request.method == "POST":
        name = request.POST.get("name").strip()
        email = request.POST.get("email").strip().lower()
        password1 = request.POST.get("password1").strip()
        password2 = request.POST.get("password2").strip()
        referralcode = request.POST.get("referral_code", "").strip()
        terms = request.POST.get("terms")

        # Name validation
        if not name:
            messages.error(request, "Name is required")
        elif len(name) < 3:
            messages.error(request, "Name must be at least 3 characters")
        elif not re.match(r'^[A-Za-z ]+$', name):
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
        elif not re.search(r'[A-Z]', password1):
            messages.error(request, "Password must contain 1 uppercase letter")
        elif not re.search(r'[0-9]', password1):
            messages.error(request, "Password must contain 1 number")
        
        # Confirm password
        elif password1 != password2:
            messages.error(request, "Passwords do not match")

        # Terms
        elif not terms:
            messages.error(request, "You must accept terms")

        # Store data in session
        else:
            request.session['temp_user_data'] = {
                'name': name,
                'email': email,
                'password': password1
            }

            # Generate OTP
            otp = str(random.randint(100000, 999999))

            # Stores in session
            request.session['otp'] = otp
            request.session['otp_created_at'] = str(timezone.now())
            
            #Send email
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

            send_mail(subject, message, 'fashio@gmail.com', [email])
        
            return redirect('user_verify_signup_otp')


    return render(request, 'user_panel/signup.html', {'temp_data' : request.POST})

@never_cache
def user_verify_signup_otp(request):

    # if user already logged in
    if request.user.is_authenticated:
        return redirect('user_loggedin_dashboard')

    # Check session data exists
    if 'temp_user_data' not in request.session:
        return redirect('user_signup')
    
    # Initialize timer
    remaining_seconds = 0
    otp_created_at_str = request.session.get('otp_created_at') # Get OTP creation time from session
    
    if otp_created_at_str:
        # Convert the stored string back into a datetime
        otp_created_at = timezone.datetime.fromisoformat(otp_created_at_str)
        diff = (timezone.now() - otp_created_at).total_seconds()
        if diff < 60:
            remaining_seconds = int(60 - diff)


    if request.method == "POST":
        user_otp = (
            request.POST.get('otp1', '') +
            request.POST.get('otp2', '') +
            request.POST.get('otp3', '') +
            request.POST.get('otp4', '') +
            request.POST.get('otp5', '') +
            request.POST.get('otp6', '')
        )

        # Get stores OTP from the session
        stored_otp = request.session.get('otp')
        
        # If OTP is correct
        if user_otp == stored_otp:
            # OTP Correct: Create the user now
            temp_data = request.session.get('temp_user_data')

            user = User.objects.create_user(
                fullname=temp_data['name'],
                email=temp_data['email'],
                password=temp_data['password']
            )

            login(request, user, backend='django.contrib.auth.backends.ModelBackend') #Logs user in directly after OTP verification
            
            # Clear session data
            del request.session['temp_user_data']
            del request.session['otp']
            del request.session['otp_created_at']
            
            return redirect('user_loggedin_dashboard')
        else:
            messages.error(request, "Invalid OTP")
            
    return render(request, 'user_panel/verify_otp.html', {
        'remaining_seconds': remaining_seconds
    })

@login_required(login_url='user_login')
def user_profile(request):
    user = request.user
    addresses = request.user.addresses.all()
    latest_address = addresses.order_by('-created_at').first()

    open_modal = request.session.pop('open_modal', None)

    return render(request, 'user_panel/profile.html', {'user':user, 'addresses':addresses, 'latest_address':latest_address, 'open_modal': open_modal})

@login_required(login_url='user_login')
def user_add_address(request):
    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        line1 = request.POST.get('line1', '').strip()
        city = request.POST.get('city', '').strip()
        postal_code = request.POST.get('postal_code', '').strip()
        state = request.POST.get('state', '').strip()

        error = None

        if not re.match(r'^[A-Za-z ]{3,}$', name):
            error = "Enter valid name (min 3 letters)"
        elif not re.match(r'^\d{10}$', phone):
            error = "Enter valid 10-digit phone number"
        elif len(line1) < 5:
            error = "Address must be at least 5 characters"
        elif not re.match(r'^[A-Za-z ]+$', city):
            error = "Enter valid city"
        elif not re.match(r'^\d{6}$', postal_code):
            error = "Enter valid postal code"
        elif not state:
            error = "Please select a state"

        if error:
            messages.error(request, error)
            addresses = request.user.addresses.all()
            return render(request, 'user_panel/profile.html', {
                'user': request.user,
                'addresses': addresses,
                'latest_address': addresses.order_by('-created_at').first(),  # ← add this
                'open_modal': 'new-address',
                'new_address_data': request.POST,
            })

        Address.objects.create(
            user=request.user,
            name=name, phone=phone, line1=line1,
            city=city, postal_code=postal_code, state=state,
        )
        

    return redirect('user_profile')


@login_required(login_url='user_login')
def user_edit_address(request, id):
    address = Address.objects.get(id=id, user=request.user)

    if request.method == "POST":
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        line1 = request.POST.get('line1', '').strip()
        city = request.POST.get('city', '').strip()
        postal_code = request.POST.get('postal_code', '').strip()
        state = request.POST.get('state', '').strip()

        error = None

        if not re.match(r'^[A-Za-z ]{3,}$', name):
            error = "Enter valid name (min 3 letters)"
        elif not re.match(r'^\d{10}$', phone):
            error = "Enter valid 10-digit phone number"
        elif len(line1) < 5:
            error = "Address must be at least 5 characters"
        elif not re.match(r'^[A-Za-z ]+$', city):
            error = "Enter valid city"
        elif not re.match(r'^\d{6}$', postal_code):
            error = "Enter valid postal code"
        elif not state:
            error = "Please select a state"

        if error:
            messages.error(request, error)
            addresses = request.user.addresses.all()
            return render(request, 'user_panel/profile.html', {
                'user': request.user,
                'addresses': addresses,
                'latest_address': addresses.order_by('-created_at').first(),  # ← add this
                'open_modal': 'edit-address',
                'edit_address_data': request.POST,
                'edit_address_id': id,
            })

        address.name = name
        address.phone = phone
        address.line1 = line1
        address.city = city
        address.postal_code = postal_code
        address.state = state
        address.save()
        

    return redirect('user_profile')

@login_required(login_url='user_login')
def user_delete_address(request, id):

    address = Address.objects.get(id=id, user=request.user)
    address.delete()
    return redirect('user_profile')

@login_required(login_url='user_login')
def user_edit_profile(request):

    user = request.user
    email_error = request.session.pop('email_edit_error', None)
    show_email_field = email_error is not None

    if request.method == "POST":
        fullname = request.POST.get('fullname', '').strip()
        phone = request.POST.get('phone', '').strip()

        # Fullname validation
        if not fullname:
            messages.error(request, "Full name is required")
            return render(request, 'user_panel/edit_profile.html', {'user': user})
        elif len(fullname) < 3:
            messages.error(request, "Full name must be at least 3 characters")
            return render(request, 'user_panel/edit_profile.html', {'user': user})
        elif not re.match(r'^[A-Za-z ]+$', fullname):
            messages.error(request, "Full name should contain only letters")
            return render(request, 'user_panel/edit_profile.html', {'user': user})

        # Phone validation
        if not re.match(r'^\d{10}$', phone):
            messages.error(request, "Enter a valid 10-digit phone number")
            return render(request, 'user_panel/edit_profile.html', {'user': user})

        # Profile image validation
        if request.FILES.get('image'):
            image = request.FILES['image']
            allowed_types = ['image/jpeg', 'image/png', 'image/webp']
            if image.content_type not in allowed_types:
                messages.error(request, "Only JPG, PNG, or WEBP images allowed")
                return render(request, 'user_panel/edit_profile.html', {'user': user})
            if image.size > 2 * 1024 * 1024:
                messages.error(request, "Image must be under 2MB")
                return render(request, 'user_panel/edit_profile.html', {'user': user})
            user.image = image

        # Phone validation
        if not re.match(r'^\d{10}$', phone):
            messages.error(request, "Enter a valid 10-digit phone number")
            return render(request, 'user_panel/edit_profile.html', {'user': user})

        # Phone duplicate check
        if User.objects.filter(phone=phone).exclude(pk=user.pk).exists():
            messages.error(request, "This phone number is already in use")
            return render(request, 'user_panel/edit_profile.html', {'user': user})
        

        # Password — only for email users
        if user.auth_provider == 'email':
            current = request.POST.get('current_password', '').strip()
            new = request.POST.get('new_password', '').strip()
            confirm = request.POST.get('confirm_password', '').strip()

            if current or new or confirm:
                if not current:
                    messages.error(request, "Enter your current password")
                    return render(request, 'user_panel/edit_profile.html', {'user': user})
                if not check_password(current, user.password):
                    messages.error(request, "Current password is incorrect")
                    return render(request, 'user_panel/edit_profile.html', {'user': user})
                if not new:
                    messages.error(request, "Enter a new password")
                    return render(request, 'user_panel/edit_profile.html', {'user': user})
                if len(new) < 6:
                    messages.error(request, "New password must be at least 6 characters")
                    return render(request, 'user_panel/edit_profile.html', {'user': user})
                if not re.search(r'[A-Z]', new):
                    messages.error(request, "New password must contain at least 1 uppercase letter")
                    return render(request, 'user_panel/edit_profile.html', {'user': user})
                if not re.search(r'[0-9]', new):
                    messages.error(request, "New password must contain at least 1 number")
                    return render(request, 'user_panel/edit_profile.html', {'user': user})
                if new != confirm:
                    messages.error(request, "Passwords do not match")
                    return render(request, 'user_panel/edit_profile.html', {'user': user})

                user.set_password(new)

        user.fullname = fullname
        user.phone = phone
        user.save()

        if user.auth_provider == 'email':
            update_session_auth_hash(request, user)

        messages.success(request, "Profile updated successfully")
        return redirect('user_profile')

    return render(request, 'user_panel/edit_profile.html', {'user': user, 'show_email_field': show_email_field, 'email_error': email_error,})

@login_required(login_url='user_login')
def user_edit_email(request):
    """Handles the email change request from edit profile form"""
    user = request.user

    if request.method == "POST":
        new_email = request.POST.get('new_email', '').strip().lower()

        # Validation
        if not new_email:
            request.session['email_edit_error'] = "Email is required"
            return redirect('user_edit_profile')
        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', new_email):
            request.session['email_edit_error'] = "Email is required" 
            return redirect('user_edit_profile')

        if new_email == user.email:
            request.session['email_edit_error'] = "New email is same as current email"
            return redirect('user_edit_profile')

        if User.objects.filter(email=new_email).exists():
            request.session['email_edit_error'] = "Email already in use"
            return redirect('user_edit_profile')

        # Generate OTP
        otp = str(random.randint(100000, 999999))

        # Store in session
        request.session['email_edit_new_email'] = new_email
        request.session['email_edit_otp'] = otp
        request.session['email_edit_otp_created_at'] = str(timezone.now())

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
        send_mail(subject, message, 'fashio@gmail.com', [new_email], fail_silently=False)

        return redirect('user_verify_email_edit_otp')

    return redirect('user_edit_profile')

@login_required(login_url='user_login')
@never_cache
def user_verify_email_edit_otp(request):
    """Verifies OTP and updates the email"""

    # Check session exists
    new_email = request.session.get('email_edit_new_email')
    if not new_email:
        return redirect('user_edit_profile')

    # Timer
    remaining_seconds = 0
    otp_created_at_str = request.session.get('email_edit_otp_created_at')
    if otp_created_at_str:
        otp_created_at = timezone.datetime.fromisoformat(otp_created_at_str)
        diff = (timezone.now() - otp_created_at).total_seconds()
        if diff < 60:
            remaining_seconds = int(60 - diff)

    if request.method == "POST":
        user_otp = (
            request.POST.get('otp1', '') +
            request.POST.get('otp2', '') +
            request.POST.get('otp3', '') +
            request.POST.get('otp4', '') +
            request.POST.get('otp5', '') +
            request.POST.get('otp6', '')
        )

        stored_otp = request.session.get('email_edit_otp')
        otp_created_at_str = request.session.get('email_edit_otp_created_at')
        otp_created_at = timezone.datetime.fromisoformat(otp_created_at_str)

        # Check expiry
        if timezone.now() - otp_created_at >= timedelta(minutes=5):
            messages.error(request, "OTP expired. Please try again.")
            # Clear session
            del request.session['email_edit_new_email']
            del request.session['email_edit_otp']
            del request.session['email_edit_otp_created_at']
            return redirect('user_edit_profile')

        if user_otp == stored_otp:
            # Update email
            user = request.user
            user.email = new_email
            user.save(update_fields=['email'])

            # Clear session
            del request.session['email_edit_new_email']
            del request.session['email_edit_otp']
            del request.session['email_edit_otp_created_at']

            # Keep user logged in
            update_session_auth_hash(request, user)

            messages.success(request, "Email updated successfully")
            return redirect('user_profile')

        else:
            messages.error(request, "Invalid OTP. Please try again.")

    return render(request, 'user_panel/verify_otp.html', {
        'remaining_seconds': remaining_seconds,
        'new_email': new_email,
    })

