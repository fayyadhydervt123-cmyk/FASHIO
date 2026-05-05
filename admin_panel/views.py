from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from django.core.paginator import Paginator
from datetime import timedelta
from django.contrib.auth import get_user_model, authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.views.decorators.cache import never_cache

# Create your views here.

User = get_user_model() #Gets the custom User model

#Used to check the user is authenticated and user have admin priorities or is admin
def is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


@never_cache
def admin_login(request):
    # If already logged in as staff → redirect
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('admin_dashboard')

    # Handle form submission
    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")

        #Authentication
        user = authenticate(request, username=email, password=password)

        if user is not None and user.is_staff:
            login(request, user) #Creates session, Stores user ID in session, Sends session cookie to browser
            return redirect('admin_dashboard')
        else:
            messages.error(request, "Invalid credentials or not authorized")

    return render(request, "admin_panel/login.html")


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def admin_dashboard(request):
    return render(request, 'admin_panel/dashboard.html')

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def admin_logout(request):
    logout(request) #Clears session data, Deletes session cookie, User becomes anonymous
    return redirect("admin_login")

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def admin_customerlist(request):

    #FETCHES DATA FROM THE URL TO USE IN (example : /customers?q=rahul&status=active&date=last_7_days)
    search_query = request.GET.get("q", "").strip() #Looks for q in url, if not fount returns empty string ""
    status_filter = request.GET.get("status", "")
    date_filter = request.GET.get("date", "")

    userlist = User.objects.filter(is_staff=False, is_superuser=False) #Only fetches normal users from User Model

    # SEARCH
    if search_query:
        userlist = userlist.filter(
            Q(fullname__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(phone__icontains=search_query)
        )

    # STATUS FILTER
    if status_filter == "active":
        userlist = userlist.filter(is_active=True)
    elif status_filter == "inactive":
        userlist = userlist.filter(is_active=False)

    # DATE FILTER
    today = timezone.now().date()

    if date_filter == "today":
        userlist = userlist.filter(date_joined__date=today)
    elif date_filter == "last_7_days":
        userlist = userlist.filter(date_joined__gte=timezone.now() - timedelta(days=7))
    elif date_filter == "last_30_days":
        userlist = userlist.filter(date_joined__gte=timezone.now() - timedelta(days=30))

    userlist = userlist.order_by('-date_joined') #New users come first

    # PAGINATION
    paginator = Paginator(userlist, 8) #Create Paginator, 8 user per page from userlist
    page_number = request.GET.get("page") #Get current page number from url (example: /customers?page=1)
    page_obj = paginator.get_page(page_number)#Get page data

    return render(request,'admin_panel/customers.html', {
        "userlist": page_obj,
        "search_query": search_query,
        "status_filter": status_filter,
        "date_filter": date_filter
        })

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def admin_user_details(request, user_id):

    try:
        user = User.objects.get(id=user_id, is_staff=False, is_superuser=False) #Gets only normal user
    #if not found
    except User.DoesNotExist:
        messages.error(request, "User not found")
        return redirect('admin_customerlist')

    # Get addresses
    addresses = user.addresses.all()

    #Send to template
    context = {
        "user_obj": user,
        "addresses": addresses,
    }

    return render(request, 'admin_panel/user_details.html', context)

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def toggle_user_status(request, user_id):
    user = User.objects.get(id=user_id) #Get user

    user.is_active = not user.is_active #If active → becomes inactive, If inactive → becomes active
    user.save()#Then save changes

    return redirect('admin_user_details', user_id=user.id)
