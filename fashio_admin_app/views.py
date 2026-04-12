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

User = get_user_model()

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

        user = authenticate(request, username=email, password=password)

        if user is not None and user.is_staff:
            login(request, user)
            return redirect('admin_dashboard')
        else:
            messages.error(request, "Invalid credentials or not authorized")

    return render(request, "admin_panel/login.html")


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def admin_dashboard(request):
    return render(request, 'admin_panel/dashboard.html')


def admin_logout(request):
    logout(request)
    return redirect("admin_login")

@user_passes_test(is_admin, login_url='admin_login')
def admin_customerlist(request):

    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    date_filter = request.GET.get("date", "")

    userlist = User.objects.filter(is_staff=False)

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
    elif status_filter in ["inactive", "blocked"]:
        userlist = userlist.filter(is_active=False)

    # DATE FILTER
    today = timezone.now().date()

    if date_filter == "today":
        userlist = userlist.filter(date_joined__date=today)
    elif date_filter == "last_7_days":
        userlist = userlist.filter(date_joined__gte=timezone.now() - timedelta(days=7))
    elif date_filter == "last_30_days":
        userlist = userlist.filter(date_joined__gte=timezone.now() - timedelta(days=30))

    userlist = userlist.order_by('-date_joined')

    # PAGINATION
    paginator = Paginator(userlist, 8)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request,'admin_panel/customers.html', {
        "userlist": page_obj,
        "search_query": search_query,
        "status_filter": status_filter,
        "date_filter": date_filter
        })
