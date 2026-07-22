from django.shortcuts import render
from products.models import Category
# Create your views here.

def about(request):

    categories = Category.objects.filter(is_blocked=False).order_by("-created_at")

    return render(request, 'about.html', {'categories': categories})

def contact(request):

    categories = Category.objects.filter(is_blocked=False).order_by("-created_at")

    return render(request, 'contact.html', {'categories': categories})