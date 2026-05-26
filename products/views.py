from django.shortcuts import render, redirect, get_object_or_404 #Used to get one object from the database.
#If the object does not exist, Django shows a 404 page.
from django.core.paginator import Paginator #Used for pagination
from django.urls import reverse #to get a URL from a URL name
from django.http import JsonResponse
from .models import *
from django.http import HttpResponseNotAllowed #when the user sends a wrong request method
from django.db.models import Sum, Min, Q, Prefetch #database query helpers
from django.contrib import messages #to show success/error messages
from django.contrib.auth.decorators import user_passes_test, login_required
from django.views.decorators.cache import never_cache
import json #Python built-in module used to read or create JSON data.
import re
from PIL import Image #Used for image handling, resizing, checking image dimensions, converting formats, etc.
from decimal import Decimal, InvalidOperation

MAX_QUANTITY_PER_ORDER = 5

def is_admin(user):
    """True only for authenticated staff / superusers."""
    return user.is_authenticated and (user.is_staff or user.is_superuser)


#helper function, checks whether the uploaded image is valid or not
def _validate_image(image):

    ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp'] #defines which image file types are allowed
    MAX_IMAGE_SIZE_MB = 10 #uploaded image should be maximum 10 MB

    if image is None: #if no image is uploaded
        return True, None

    if image.content_type not in ALLOWED_IMAGE_TYPES: #checks file type
        return False, 'Only JPG, PNG, or WEBP images are allowed.'

    if image.size > MAX_IMAGE_SIZE_MB * 1024 * 1024: #checks image size
        return False, f'Image must be smaller than {MAX_IMAGE_SIZE_MB} MB.'

    try:
        img = Image.open(image)
        img.verify()

        # RESET FILE POINTER
        image.seek(0)

    except Exception:
        return False, 'Invalid or corrupted image.'

    return True, None





# ─────────────────────────────────────────────────────────────
# Admin Side - Category Management
# ─────────────────────────────────────────────────────────────

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def view_categories(request):

    query = request.GET.get('q', '').strip() #This line reads the search text from the URL

    categories = Category.objects.prefetch_related( #Gets category records from the database
        'subcategories'
    ).order_by('name') 
    
    #Search filter
    if query:
        categories = categories.filter(name__icontains=query)

    paginator   = Paginator(categories, 10) #Show 10 per page
    page_number = request.GET.get('page', 1) #Gets page number from url
    page_obj    = paginator.get_page(page_number) #Gets the correct page data

    return render(request, 'categories/categories.html', {
        'categories': page_obj,
        'query': query,
    })


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def add_category(request):
    if request.method == 'POST': #Check request method
        name        = request.POST.get('name', '').strip() #Get name from form input
        description = request.POST.get('description', '').strip()
        image       = request.FILES.get('image')

        # ── Name validation ──────────────────────────────────
        if not name:
            messages.error(request, 'Category name is required.')
            return redirect('categories')

        # ── Minimum length validation ────────────────────────
        if len(name) < 3:
            messages.error(request, 'Category name must be at least 3 characters.')
            return redirect('categories')

        # ── Duplicate check (case-insensitive) ───────────────
        if Category.objects.filter(name__iexact=name).exists():
            messages.error(request, f'Category "{name}" already exists.')
            return redirect('categories')

        # ── Image validation ─────────────────────────────────
        image_ok, image_err = _validate_image(image) #This calls the helper function
        if not image_ok:
            messages.error(request, image_err)
            return redirect('categories')

        #Creates a new row in category table
        Category.objects.create(name=name, description=description, image=image)
        messages.success(request, f'Category "{name}" added successfully.')
        return redirect('categories')

    return redirect('categories')


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def edit_category(request):
    if request.method == 'POST':
        category_id = request.POST.get('category_id', '').strip() #Gets the category ID from the hidden input from submitted form
        name        = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        image       = request.FILES.get('image')

        # ── Checks category_id exists ────────────────────────
        if not category_id:
            messages.error(request, 'Invalid request: category ID is missing.')
            return redirect('categories')

        # ── Name validation ──────────────────────────────────
        if not name:
            messages.error(request, 'Category name is required.')
            return redirect('categories')

        # ── Minimum length validation ────────────────────────
        if len(name) < 3:
            messages.error(request, 'Category name must be at least 3 characters.')
            return redirect('categories')

        # ── Fetch category from database using category_id ───
        try:
            category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            messages.error(request, 'Category not found.')
            return redirect('categories')

        # ── Duplicate check — exclude the current category ───
        if Category.objects.filter(name__iexact=name).exclude(id=category_id).exists():
            messages.error(request, f'Category "{name}" already exists.')
            return redirect('categories')

        # ── Image validation ─────────────────────────────────
        image_ok, image_err = _validate_image(image)
        if not image_ok:
            messages.error(request, image_err)
            return redirect('categories')

        category.name        = name #updates category name
        category.description = description #updates category description
        if image:
            category.image = image #if image, it also
        category.save() #saves the updated object into the database

        messages.success(request, f'Category "{name}" updated successfully.')
        return redirect('categories')

    return redirect('categories')


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def toggle_category_block(request):
    if request.method == 'POST':
        category_id = request.POST.get('category_id', '').strip() #get category_id 

        if not category_id: #check if category_id is missing
            messages.error(request, 'Invalid request: category ID is missing.')
            return redirect('categories')

        try:
            category = Category.objects.get(id=category_id) #fetch category from database
        except Category.DoesNotExist:
            messages.error(request, 'Category not found.')
            return redirect('categories')

        category.is_blocked = not category.is_blocked #Flips the current value
        category.save()

        action = 'blocked' if category.is_blocked else 'unblocked'
        messages.success(request, f'Category "{category.name}" {action} successfully.')
        return redirect('categories')

    return redirect('categories')


# ─────────────────────────────────────────────────────────────
# Admin Side - Product Management
# ─────────────────────────────────────────────────────────────


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def view_products(request):
    query        = request.GET.get('q', '').strip()
    category_id  = request.GET.get('category', '').strip()
    stock_status = request.GET.get('stock', '').strip()
    price_range  = request.GET.get('price', '').strip()


    active_variants_prefetch = Prefetch(
        'variants',
        queryset=ProductVariant.objects.filter(
            status='ACTIVE'
        ).prefetch_related('images'),
        to_attr='active_variants_list'
    )

    products = (
        Product.objects
        .select_related('subcategory__category')
        .prefetch_related(active_variants_prefetch)
        .annotate(
            total_stock=Sum(
                'variants__stock',
                filter=Q(variants__status='ACTIVE')
            )
        )
        .distinct()
        .order_by('-created_at')
    )

    if query:
        products = products.filter(Q(name__icontains=query))

    if category_id:
        products = products.filter(subcategory__category__id=category_id)

    if stock_status == 'in_stock':
        products = products.filter(total_stock__gt=0)
    elif stock_status == 'out_of_stock':
        products = products.filter(Q(total_stock=0) | Q(total_stock__isnull=True))

    if price_range == 'under_200':
        products = products.filter(base_price__lt=200)
    elif price_range == '200_400':
        products = products.filter(base_price__gte=200, base_price__lte=400)
    elif price_range == 'above_400':
        products = products.filter(base_price__gt=400)

    paginator   = Paginator(products, 10)
    page_number = request.GET.get('page', 1)
    page_obj    = paginator.get_page(page_number)

    for product in page_obj:
        active_variants = product.active_variants_list
        product.display_status = 'ACTIVE' if (product.is_active and active_variants) else 'INACTIVE'
        first_variant  = active_variants[0] if active_variants else None
        first_image    = first_variant.images.first() if first_variant and first_variant.images.all() else None

    categories = Category.objects.filter(is_blocked=False).order_by('name')

    return render(request, 'products/products.html', {
        'products': page_obj,
        'categories': categories,
        'query': query,
        'category_id': category_id,
        'stock_status': stock_status,
        'price_range': price_range,
    })

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def add_product(request):
    categories = Category.objects.filter(is_blocked=False).order_by('name')
    subcategories_json = {
        str(cat.id): [
            {'id': str(s.id), 'name': s.name}
            for s in cat.subcategories.order_by('name')
        ]
        for cat in categories
    }
    base_context = {
        'categories': categories,
        'subcategories_json': json.dumps(subcategories_json),
    }

    if request.method == 'POST':
        name           = request.POST.get('name', '').strip()
        base_price     = request.POST.get('base_price', '').strip()
        description    = request.POST.get('description', '').strip()
        subcategory_id = request.POST.get('subcategory_id', '').strip()
        category_id    = request.POST.get('category_id', '').strip()
        action         = request.POST.get('action', 'save')

        product_details = {
            'material':      request.POST.get('material', '').strip(),
            'fabric_weight': request.POST.get('fabric_weight', '').strip(),
            'fit':           request.POST.get('fit', '').strip(),
            'design':        request.POST.get('design', '').strip(),
            'care':          request.POST.get('care', '').strip(),
            'durability':    request.POST.get('durability', '').strip(),
        }

        errors = {}

        # ── Name ────────────────────────────────────────────
        if not name:
            errors['name'] = 'Product name is required.'
        elif len(name) < 3:
            errors['name'] = 'Must be at least 3 characters.'
        elif Product.objects.filter(name__iexact=name).exists():
            errors['name'] = f'A product named "{name}" already exists.'

        # ── Price ────────────────────────────────────────────
        if not base_price:
            errors['base_price'] = 'Base price is required.'
        else:
            try:
                base_price_val = Decimal(base_price)
                if base_price_val <= 0:
                    errors['base_price'] = 'Price must be greater than 0.'
            except InvalidOperation:
                errors['base_price'] = 'Enter a valid number.'

        # ── Description ──────────────────────────────────────
        if not description:
            errors['description'] = 'Description is required.'
        elif len(description) < 10:
            errors['description'] = 'Description must be at least 10 characters.'

        # ── Subcategory ──────────────────────────────────────
        if not subcategory_id:
            errors['subcategory_id'] = 'Please select a subcategory.'
        else:
            try:
                subcategory = SubCategory.objects.get(id=subcategory_id)
            except SubCategory.DoesNotExist:
                errors['subcategory_id'] = 'Selected subcategory not found.'

        if errors:
            return render(request, 'products/add_product.html', {
                **base_context,
                'errors': errors,
                'form_data': request.POST,  # ← sends data back to repopulate fields
                'selected_category_id': category_id,
            })

        product = Product.objects.create(
            name=name,
            base_price=base_price_val,
            description=description,
            subcategory=subcategory,
            product_details=product_details,
            is_active=True,
        )

        messages.success(request, f'Product "{name}" created successfully.')

        if action == 'save_add_variant':
            return redirect('add_variant', product_id=product.id)

        return redirect('products')

    return render(request, 'products/add_product.html', base_context)

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def edit_product(request, product_id):
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        messages.error(request, 'Product not found.')
        return redirect('products')

    categories = Category.objects.filter(is_blocked=False).order_by('name')
    subcategories_json = {
        str(cat.id): [
            {'id': str(s.id), 'name': s.name}
            for s in cat.subcategories.order_by('name')
        ]
        for cat in categories
    }

    base_context = {
        'categories': categories,
        'subcategories_json': json.dumps(subcategories_json),
        'product': product,
        # Pre-fill form_data from existing product so the template repopulates
        'form_data': {
            'name':           product.name,
            'base_price':     product.base_price,
            'description':    product.description,
            'category_id':    str(product.subcategory.category.id) if product.subcategory else '',
            'subcategory_id': str(product.subcategory.id) if product.subcategory else '',
            'material':       product.product_details.get('material', ''),
            'fabric_weight':  product.product_details.get('fabric_weight', ''),
            'fit':            product.product_details.get('fit', ''),
            'design':         product.product_details.get('design', ''),
            'care':           product.product_details.get('care', ''),
            'durability':     product.product_details.get('durability', ''),
        },
    }

    if request.method == 'POST':
        name           = request.POST.get('name', '').strip()
        base_price     = request.POST.get('base_price', '').strip()
        description    = request.POST.get('description', '').strip()
        subcategory_id = request.POST.get('subcategory_id', '').strip()
        category_id    = request.POST.get('category_id', '').strip()

        product_details = {
            'material':      request.POST.get('material', '').strip(),
            'fabric_weight': request.POST.get('fabric_weight', '').strip(),
            'fit':           request.POST.get('fit', '').strip(),
            'design':        request.POST.get('design', '').strip(),
            'care':          request.POST.get('care', '').strip(),
            'durability':    request.POST.get('durability', '').strip(),
        }

        errors = {}

        if not name:
            errors['name'] = 'Product name is required.'
        elif len(name) < 3:
            errors['name'] = 'Must be at least 3 characters.'
        elif Product.objects.filter(name__iexact=name).exclude(id=product_id).exists():
            errors['name'] = f'A product named "{name}" already exists.'

        if not base_price:
            errors['base_price'] = 'Base price is required.'
        else:
            try:
                price_val = Decimal(base_price)

                if price_val <= 0:
                    errors['base_price'] = 'Price must be greater than 0.'

            except InvalidOperation:
                errors['base_price'] = 'Enter a valid price.'

        if not description:
            errors['description'] = 'Description is required.'
        elif len(description) < 10:
            errors['description'] = 'Description must be at least 10 characters.'

        if not subcategory_id:
            errors['subcategory_id'] = 'Please select a subcategory.'
        else:
            try:
                subcategory = SubCategory.objects.get(id=subcategory_id)
            except SubCategory.DoesNotExist:
                errors['subcategory_id'] = 'Selected subcategory not found.'

        if errors:
            return render(request, 'products/edit_product.html', {
                **base_context,
                'errors': errors,
                'form_data': request.POST,
            })

        product.name            = name
        product.base_price      = price_val
        product.description     = description
        product.subcategory     = subcategory
        product.product_details = product_details
        product.save()

        messages.success(request, f'Product "{name}" updated successfully.')
        return redirect('products')

    return render(request, 'products/edit_product.html', base_context)

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def toggle_product_block(request):
    if request.method == 'POST':
        product_id = request.POST.get('product_id', '').strip()

        if not product_id:
            messages.error(request, 'Invalid request: product ID is missing.')
            return redirect('products')

        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            messages.error(request, 'Product not found.')
            return redirect('products')

        product.is_active = not product.is_active
        product.save()

        action = 'unblocked' if product.is_active else 'blocked'
        messages.success(request, f'Product "{product.name}" {action} successfully.')
        return redirect('products')

    return redirect('products')


# ─────────────────────────────────────────────────────────────
# Admin Side - Subcategory Management
# ─────────────────────────────────────────────────────────────


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def view_subcategories(request):
    # Each category gets its own search query and paginated subcategory list
    categories = Category.objects.order_by('name').prefetch_related('subcategories')

    sections = []
    for cat in categories:
        q = request.GET.get(f'q_{cat.id}', '').strip()
        subcats = cat.subcategories.all().order_by('created_at')

        if q:
            subcats = subcats.filter(name__icontains=q)

        paginator   = Paginator(subcats, 5)
        page_number = request.GET.get(f'page_{cat.id}', 1)
        page_obj    = paginator.get_page(page_number)

        sections.append({
            'category': cat,
            'subcategories': page_obj,
            'query': q,
        })

    return render(request, 'products/subcategories.html', {
        'sections': sections,
    })


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def add_subcategory(request):
    if request.method == 'POST':
        name        = request.POST.get('name', '').strip()
        category_id = request.POST.get('category_id', '').strip()

        if not name:
            messages.error(request, 'Subcategory name is required.')
            return redirect('subcategories')

        if len(name) < 2:
            messages.error(request, 'Subcategory name must be at least 2 characters.')
            return redirect('subcategories')

        if not category_id:
            messages.error(request, 'Please select a category.')
            return redirect('subcategories')

        try:
            category = Category.objects.get(id=category_id, is_blocked=False)
        except Category.DoesNotExist:
            messages.error(request, 'Category not found.')
            return redirect('subcategories')

        if SubCategory.objects.filter(name__iexact=name, category=category).exists():
            messages.error(request, f'Subcategory "{name}" already exists in {category.name}.')
            return redirect('subcategories')

        SubCategory.objects.create(name=name, category=category)
        messages.success(request, f'Subcategory "{name}" added to {category.name}.')
        return redirect('subcategories')

    return redirect('subcategories')


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def edit_subcategory(request):
    if request.method != 'POST':                          # ← 405 instead of silent redirect
        return HttpResponseNotAllowed(['POST'])

    subcat_id = request.POST.get('subcat_id', '').strip()
    name      = request.POST.get('name', '').strip()
    # category_id removed — it was accepted but never used

    if not subcat_id:
        messages.error(request, 'Invalid request: subcategory ID missing.')
        return redirect('subcategories')

    if not name:
        messages.error(request, 'Subcategory name is required.')
        return redirect('subcategories')

    if len(name) < 2:
        messages.error(request, 'Subcategory name must be at least 2 characters.')
        return redirect('subcategories')

    try:
        subcat = SubCategory.objects.select_related('category').get(id=subcat_id)
    except SubCategory.DoesNotExist:
        messages.error(request, 'Subcategory not found.')
        return redirect('subcategories')

    if SubCategory.objects.filter(
        name__iexact=name, category=subcat.category
    ).exclude(id=subcat_id).exists():
        messages.error(request, f'Subcategory "{name}" already exists in {subcat.category.name}.')
        return redirect('subcategories')

    subcat.name = name
    subcat.save()

    messages.success(request, f'Subcategory updated to "{name}".')
    return redirect('subcategories')

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def delete_subcategory(request):
    if request.method == 'POST':
        subcat_id = request.POST.get('subcat_id', '').strip()

        if not subcat_id:
            messages.error(request, 'Invalid request: subcategory ID missing.')
            return redirect('subcategories')

        try:
            subcat = SubCategory.objects.select_related('category').get(id=subcat_id)
        except SubCategory.DoesNotExist:
            messages.error(request, 'Subcategory not found.')
            return redirect('subcategories')

        # Guard: block deletion if products are linked
        if subcat.products.exists():
            messages.error(request, f'Cannot delete "{subcat.name}" — it has products assigned to it.')
            return redirect('subcategories')

        name = subcat.name
        cat_name = subcat.category.name
        subcat.delete()
        messages.success(request, f'Subcategory "{name}" deleted from {cat_name}.')
        return redirect('subcategories')

    return redirect('subcategories')


# ─────────────────────────────────────────────────────────────
# Admin Side - Variant Management
# ─────────────────────────────────────────────────────────────


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def product_variants(request, product_id):
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        messages.error(request, 'Product not found.')
        return redirect('products')

    variants = product.variants.prefetch_related('images').order_by('created_at')

    # thumbnail for summary card
    default_variant = variants.filter(status='ACTIVE', is_default=True).first()
    first_active    = default_variant or variants.filter(status='ACTIVE').first()
    first_image     = first_active.images.first() if first_active else None
    product.thumbnail = first_image.image.url if first_image else None
    product.total_stock  = variants.filter(status='ACTIVE').aggregate(total=Sum('stock'))['total'] or 0
    product.display_status = (
        'ACTIVE'
        if product.is_active and variants.filter(status='ACTIVE').exists()
        else 'INACTIVE'
    )

    return render(request, 'products/product_variants.html', {
        'product': product,
        'variants': variants,
    })

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def add_variant(request, product_id):
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        messages.error(request, 'Product not found.')
        return redirect('products')

    # Attach thumbnail for the summary card
    first_variant = (
        product.variants.filter(status='ACTIVE', is_default=True).first()
        or product.variants.filter(status='ACTIVE').first()
    )
    first_image   = first_variant.images.first() if first_variant else None
    product.total_stock = product.variants.filter(status='ACTIVE').aggregate(
        total=Sum('stock'))['total'] or 0

    size_choices = ProductVariant.SIZE_CHOICES

    if request.method == 'POST':
        color     = request.POST.get('color', '').strip()
        color_hex = request.POST.get('color_hex', '').strip()
        size      = request.POST.get('size', '').strip()
        price     = request.POST.get('price', '').strip()
        stock     = request.POST.get('stock', '0').strip()
        status    = request.POST.get('status', 'ACTIVE')
        is_default = request.POST.get('is_default') == 'on'
        images     = request.FILES.getlist('images')        

        errors = {}

        if not color:
            errors['color'] = 'Color display name is required.'
        if not size:
            errors['size'] = 'Please select a size.'

        price_val = None
        if price:
            try:
                price_val = float(price)
                if price_val <= 0:
                    errors['price'] = 'Price must be greater than 0.'
            except ValueError:
                errors['price'] = 'Enter a valid price.'

        try:
            stock_val = int(stock)
            if stock_val < 0:
                errors['stock'] = 'Stock cannot be negative.'
        except ValueError:
            errors['stock'] = 'Enter a valid stock quantity.'

        if len(images) < 3:
            errors['images'] = 'Please upload at least 3 images.'
        else:
            for img in images:
                image_ok, image_err = _validate_image(img)
                if not image_ok:
                    errors['images'] = image_err
                    break

        if not re.match(r'^#[0-9A-Fa-f]{6}$', color_hex):
            errors['color_hex'] = 'Enter a valid hex color.'

        if ProductVariant.objects.filter(
            product=product,
            color__iexact=color,
            size=size
        ).exists():
            errors['size'] = 'This variant already exists.'

        if errors:
            return render(request, 'products/add_variant.html', {
                'product': product,
                'size_choices': size_choices,
                'errors': errors,
                'form_data': request.POST,
            })
        
        if is_default:
            ProductVariant.objects.filter(
                product=product,
                is_default=True
            ).update(is_default=False)

        variant = ProductVariant.objects.create(
            product=product,
            color=color,
            color_hex=color_hex,
            size=size,
            price=price_val or product.base_price,
            stock=stock_val,
            status=status,
            is_default=is_default,
        )

        for image in images:
            ProductImage.objects.create(
                product_variant=variant,
                image=image
            )

        messages.success(request, f'Variant added successfully.')
        return redirect('product_variants', product_id=product.id)

    return render(request, 'products/add_variant.html', {
        'product': product,
        'size_choices': size_choices,
    })


@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def edit_variant(request, variant_id):
    try:
        variant = ProductVariant.objects.select_related('product').get(id=variant_id)
    except ProductVariant.DoesNotExist:
        messages.error(request, 'Variant not found.')
        return redirect('products')

    product = variant.product
    product.total_stock = product.variants.filter(status='ACTIVE').aggregate(
        total=Sum('stock'))['total'] or 0

    size_choices = ProductVariant.SIZE_CHOICES
    existing_images = variant.images.all()

    if request.method == 'POST':
        color      = request.POST.get('color', '').strip()
        color_hex  = request.POST.get('color_hex', '').strip()
        size       = request.POST.get('size', '').strip()
        price      = request.POST.get('price', '').strip()
        stock      = request.POST.get('stock', '0').strip()
        status     = request.POST.get('status', 'ACTIVE')
        is_default = request.POST.get('is_default') == 'on'
        new_images = request.FILES.getlist('images')

        # ── Image mode ──────────────────────────────────────
        keep_existing   = request.POST.get('keep_existing_images') == 'true'
        removed_ids_str = request.POST.get('removed_image_ids', '').strip()
        removed_ids     = [r.strip() for r in removed_ids_str.split(',') if r.strip()]

        errors = {}

        if not color:
            errors['color'] = 'Color display name is required.'
        if not size:
            errors['size'] = 'Please select a size.'

        price_val = None
        if price:
            try:
                price_val = float(price)
                if price_val <= 0:
                    errors['price'] = 'Price must be greater than 0.'
            except ValueError:
                errors['price'] = 'Enter a valid price.'

        try:
            stock_val = int(stock)
            if stock_val < 0:
                errors['stock'] = 'Stock cannot be negative.'
        except ValueError:
            errors['stock'] = 'Enter a valid stock quantity.'

        if not re.match(r'^#[0-9A-Fa-f]{6}$', color_hex):
            errors['color_hex'] = 'Enter a valid hex color.'

        # ── Image count validation ───────────────────────────
        if keep_existing:
            remaining = existing_images.count() - len(removed_ids)
        else:
            remaining = 0
        total_image_count = remaining + len(new_images)

        if new_images:
            for img in new_images:
                image_ok, image_err = _validate_image(img)
                if not image_ok:
                    errors['images'] = image_err
                    break

        if total_image_count < 3:
            errors['images'] = 'At least 3 images are required.'

        # ── Duplicate check ──────────────────────────────────
        if ProductVariant.objects.filter(
            product=product,
            color__iexact=color,
            size=size
        ).exclude(id=variant_id).exists():
            errors['size'] = 'This color + size combination already exists.'

        if errors:
            return render(request, 'products/edit_variant.html', {
                'product': product,
                'variant': variant,
                'size_choices': size_choices,
                'existing_images': existing_images,
                'errors': errors,
                'form_data': request.POST,
            })

        # ── Save variant ─────────────────────────────────────
        if is_default:
            ProductVariant.objects.filter(
                product=product, is_default=True
            ).exclude(id=variant_id).update(is_default=False)

        variant.color      = color
        variant.color_hex  = color_hex
        variant.size       = size
        variant.price      = price_val if price_val else product.base_price
        variant.stock      = stock_val
        variant.status     = status
        variant.is_default = is_default
        variant.save()

        # ── Apply image changes ───────────────────────────────
        if keep_existing:
            if removed_ids:
                variant.images.filter(id__in=removed_ids).delete()
        else:
            variant.images.all().delete()

        for image in new_images:
            ProductImage.objects.create(product_variant=variant, image=image)

        messages.success(request, 'Variant updated successfully.')
        return redirect('product_variants', product_id=product.id)

    return render(request, 'products/edit_variant.html', {
        'product': product,
        'variant': variant,
        'size_choices': size_choices,
        'existing_images': existing_images,
        'form_data': {
            'color':      variant.color,
            'color_hex':  variant.color_hex,
            'size':       variant.size,
            'price':      variant.price if variant.price != product.base_price else '',
            'stock':      variant.stock,
            'status':     variant.status,
            'is_default': variant.is_default,
        },
    })

@never_cache
@user_passes_test(is_admin, login_url='admin_login')
def toggle_variant_status(request):
    if request.method == 'POST':
        variant_id = request.POST.get('variant_id', '').strip()

        if not variant_id:
            messages.error(request, 'Invalid request: variant ID is missing.')
            return redirect('products')

        try:
            variant = ProductVariant.objects.select_related('product').get(id=variant_id)
        except ProductVariant.DoesNotExist:
            messages.error(request, 'Variant not found.')
            return redirect('products')

        if variant.status == 'ACTIVE':
            variant.status = 'INACTIVE'
            action = 'deactivated'
        else:
            variant.status = 'ACTIVE'
            action = 'activated'

        variant.save()
        messages.success(request, f'Variant {action} successfully.')
        return redirect('product_variants', product_id=variant.product.id)

    return redirect('products')


# ─────────────────────────────────────────────────────────────
# User Side - Product List
# ─────────────────────────────────────────────────────────────

@never_cache
@login_required(login_url='user_login')
def product_list(request):
    query = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    size = request.GET.get('size', '').strip()
    price_range = request.GET.get('price', '').strip()
    sort = request.GET.get('sort', '').strip()

    active_variants_prefetch = Prefetch(
        'variants',
        queryset=ProductVariant.objects.filter(
            status='ACTIVE'
        ).prefetch_related('images').order_by('created_at'),
        to_attr='active_variants'
    )

    products = (
        Product.objects
        .filter(
            is_active=True,
            subcategory__isnull=False,
            subcategory__category__is_blocked=False
        )
        .select_related('subcategory', 'subcategory__category')
        .prefetch_related(active_variants_prefetch)
        .order_by('-created_at')
    )

    # Search
    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(subcategory__name__icontains=query) |
            Q(subcategory__category__name__icontains=query) |
            Q(variants__sku__icontains=query) |
            Q(variants__color__icontains=query) |
            Q(variants__size__icontains=query)
        ).distinct()

    # Category filter
    if category:
        products = products.filter(subcategory__category__id=category)

    # Size filter
    if size:
        products = products.filter(
            variants__size=size,
            variants__status='ACTIVE'
        ).distinct()

    # Basic price filter using product base price
    if price_range == 'under_1000':
        products = products.filter(base_price__lt=1000)

    elif price_range == '1000_2000':
        products = products.filter(base_price__gte=1000, base_price__lte=2000)

    elif price_range == '2000_3000':
        products = products.filter(base_price__gte=2000, base_price__lte=3000)

    elif price_range == 'above_3000':
        products = products.filter(base_price__gt=3000)

    product_list_data = []

    for product in products:
        active_variants = list(product.active_variants)

        # Hide products with no active variants
        if not active_variants:
            continue

        default_variant = None

        for variant in active_variants:
            if variant.is_default:
                default_variant = variant
                break

        if not default_variant:
            default_variant = active_variants[0]

        default_image = default_variant.images.first()

        product.default_variant = default_variant
        product.thumbnail = default_image.image.url if default_image else None
        product.display_price = default_variant.discounted_price
        product.original_price = default_variant.price
        product.total_stock = sum(variant.stock for variant in active_variants)

        sizes = []

        for variant in active_variants:
            if variant.size and variant.size not in sizes:
                sizes.append(variant.size)

        product.available_sizes = sizes

        variant_data = []

        for variant in active_variants:
            first_image = variant.images.first()

            variant_data.append({
                'id': str(variant.id),
                'size': variant.size,
                'color': variant.color,
                'color_hex': variant.color_hex,
                'stock': variant.stock,
                'price': float(variant.price),
                'discount': float(variant.discount),
                'discounted_price': float(variant.discounted_price),
                'image': first_image.image.url if first_image else product.thumbnail,
            })

        product.variant_data = variant_data

        product_list_data.append(product)

    # Sorting
    if sort == 'price_low_high':
        product_list_data.sort(key=lambda product: product.display_price)

    elif sort == 'price_high_low':
        product_list_data.sort(key=lambda product: product.display_price, reverse=True)

    elif sort == 'az':
        product_list_data.sort(key=lambda product: product.name.lower())

    elif sort == 'za':
        product_list_data.sort(key=lambda product: product.name.lower(), reverse=True)

    else:
        product_list_data.sort(key=lambda product: product.created_at, reverse=True)

    paginator = Paginator(product_list_data, 12)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    modal_products_data = []

    for product in page_obj:
        modal_products_data.append({
            'id': str(product.id),
            'name': product.name,
            'thumbnail': product.thumbnail,
            'price': float(product.display_price),
            'variants': product.variant_data,
        })

    categories = Category.objects.filter(is_blocked=False).order_by('name')

    selected_category_obj = None

    if category:
        selected_category_obj = Category.objects.filter(
            id=category,
            is_blocked=False
        ).first()

    wishlist_variant_ids = list(
        Wishlist.objects.filter(user=request.user)
        .values_list('variant_id', flat=True)
    )

    return render(request, 'products/product_list.html', {
        'products': page_obj,
        'categories': categories,
        'query': query,
        'selected_category': category,
        'selected_category_obj': selected_category_obj,
        'selected_size': size,
        'selected_price': price_range,
        'selected_sort': sort,
        'modal_products_data': modal_products_data,
        'wishlist_variant_ids': wishlist_variant_ids,
    })

@never_cache
@login_required(login_url='user_login')
def product_detail(request, product_id):
    active_variants_prefetch = Prefetch(
        'variants',
        queryset=ProductVariant.objects.filter(
            status='ACTIVE'
        ).prefetch_related('images').order_by('color', 'size'),
        to_attr='active_variants'
    )

    product = (
        Product.objects.filter(
            id=product_id,
            is_active=True,
            subcategory__isnull=False,
            subcategory__category__is_blocked=False
        )
        .select_related('subcategory', 'subcategory__category')
        .prefetch_related(active_variants_prefetch)
        .first()
    )

    if not product:
        messages.error(request, 'This product is unavailable.')
        return redirect('product_list')

    variants = list(product.active_variants)

    if not variants:
        messages.error(request, 'This product is currently unavailable.')
        return redirect('product_list')

    default_variant = None

    for variant in variants:
        if variant.is_default:
            default_variant = variant
            break

    if not default_variant and variants:
        default_variant = variants[0]

    images = default_variant.images.all() if default_variant else []

    total_stock = sum(variant.stock for variant in variants)

    available_sizes = []
    available_colors = []

    for variant in variants:
        if variant.size and variant.size not in available_sizes:
            available_sizes.append(variant.size)

        color_data = {
            'name': variant.color,
            'hex': variant.color_hex,
        }

        if color_data not in available_colors:
            available_colors.append(color_data)

    related_products = []

    if product.subcategory:
        related_variants_prefetch = Prefetch(
            'variants',
            queryset=ProductVariant.objects.filter(
                status='ACTIVE'
            ).prefetch_related('images'),
            to_attr='active_variants'
        )

        related_queryset = (
            Product.objects.filter(
                is_active=True,
                subcategory=product.subcategory,
                subcategory__category__is_blocked=False
            )
            .exclude(id=product.id)
            .select_related('subcategory', 'subcategory__category')
            .prefetch_related(related_variants_prefetch)
            .order_by('-created_at')[:4]
        )

        for related in related_queryset:
            related_active_variants = list(related.active_variants)

            if not related_active_variants:
                continue

            related_default_variant = None

            for variant in related_active_variants:
                if variant.is_default:
                    related_default_variant = variant
                    break

            if not related_default_variant:
                related_default_variant = related_active_variants[0]

            related_image = related_default_variant.images.first()

            related.default_variant = related_default_variant
            related.thumbnail = related_image.image.url if related_image else None
            related.display_price = related_default_variant.discounted_price

            related_products.append(related)

    variants_data = []

    for variant in variants:
        variant_images = [
            image.image.url
            for image in variant.images.all()
        ]

        variants_data.append({
            'id': str(variant.id),
            'size': variant.size,
            'color': variant.color,
            'color_hex': variant.color_hex,
            'stock': variant.stock,
            'price': float(variant.price),
            'discount': float(variant.discount),
            'discounted_price': float(variant.discounted_price),
            'images': variant_images,
            'is_default': variant.is_default,
        })        

    return render(request, 'products/product_detail.html', {
        'product': product,
        'variants': variants,
        'default_variant': default_variant,
        'images': images,
        'total_stock': total_stock,
        'available_sizes': available_sizes,
        'available_colors': available_colors,
        'related_products': related_products,
        'variants_data': variants_data,
    })

@never_cache
@login_required(login_url='user_login')
def add_to_cart(request):
    if request.method != 'POST':
        return redirect('product_list')

    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

    variant_id = request.POST.get('variant_id', '').strip()
    quantity = request.POST.get('quantity', '1').strip()

    try:
        quantity = int(quantity)
    except ValueError:
        if is_ajax:
            return JsonResponse({
                'success': False,
                'message': 'Invalid quantity.'
            }, status=400)

        messages.error(request, 'Invalid quantity.')
        return redirect(request.META.get('HTTP_REFERER', 'product_list'))

    if quantity < 1:
        if is_ajax:
            return JsonResponse({
                'success': False,
                'message': 'Quantity must be at least 1.'
            }, status=400)

        messages.error(request, 'Quantity must be at least 1.')
        return redirect(request.META.get('HTTP_REFERER', 'product_list'))

    variant = get_object_or_404(
        ProductVariant.objects.select_related(
            'product',
            'product__subcategory',
            'product__subcategory__category'
        ),
        id=variant_id,
        status='ACTIVE',
        product__is_active=True,
        product__subcategory__isnull=False,
        product__subcategory__category__is_blocked=False
    )

    max_allowed = min(variant.stock, MAX_QUANTITY_PER_ORDER)

    if max_allowed <= 0:
        if is_ajax:
            return JsonResponse({
                'success': False,
                'message': 'This variant is out of stock.'
            }, status=400)

        messages.error(request, 'This variant is out of stock.')
        return redirect('product_detail', product_id=variant.product.id)

    if quantity > max_allowed:
        if is_ajax:
            return JsonResponse({
                'success': False,
                'message': f'Only {max_allowed} item(s) available.'
            }, status=400)

        messages.error(request, f'Only {max_allowed} item(s) available.')
        return redirect('product_detail', product_id=variant.product.id)

    cart_item, created = Cart.objects.get_or_create(
        user=request.user,
        variant=variant,
        defaults={
            'product': variant.product,
            'quantity': quantity
        }
    )

    if not created:
        new_quantity = cart_item.quantity + quantity

        if new_quantity > max_allowed:
            if is_ajax:
                return JsonResponse({
                    'success': False,
                    'message': f'You can only add up to {max_allowed} item(s) for this variant.'
                }, status=400)

            messages.error(request, f'You can only add up to {max_allowed} item(s) for this variant.')
            return redirect('cart_page')

        cart_item.quantity = new_quantity
        cart_item.save()


    # Notification count should increase for BOTH new cart item and existing cart item
    current_notification_count = request.session.get('cart_notification_count', 0)

    request.session['cart_notification_count'] = current_notification_count + 1
    request.session.modified = True

    # Remove product from wishlist after successfully adding to cart
    removed_from_wishlist = Wishlist.objects.filter(
        user=request.user,
        variant=variant
    ).delete()[0] > 0

    wishlist_notification_count = Wishlist.objects.filter(
        user=request.user
    ).count()


    if is_ajax:
        message = 'Product added to cart.'

        if removed_from_wishlist:
            message = 'Product added to cart and removed from wishlist.'

        return JsonResponse({
            'success': True,
            'message': message,
            'cart_notification_count': request.session['cart_notification_count'],
            'wishlist_notification_count': wishlist_notification_count,
            'removed_from_wishlist': removed_from_wishlist,
            'removed_variant_id': variant.id,
        })

    messages.success(request, 'Product added to cart.')
    return redirect(request.META.get('HTTP_REFERER', 'product_list'))


@login_required(login_url='user_login')
def cart_page(request):
    request.session['cart_notification_count'] = 0
    request.session.modified = True

    cart_items = (
        Cart.objects
        .filter(user=request.user)
        .select_related(
            'product',
            'variant',
            'product__subcategory',
            'product__subcategory__category'
        )
        .prefetch_related('variant__images')
        .order_by('-created_at')
    )

    total_price = 0
    total_items = 0
    can_checkout = True

    for item in cart_items:
        first_image = item.variant.images.first()
        item.thumbnail = first_image.image.url if first_image else None

        item.is_available = (
            item.product.is_active and
            item.product.subcategory is not None and
            item.product.subcategory.category.is_blocked is False and
            item.variant.status == 'ACTIVE' and
            item.variant.stock > 0
        )

        item.max_quantity = min(item.variant.stock, MAX_QUANTITY_PER_ORDER)

        if not item.is_available:
            item.unavailable_reason = 'This product is currently unavailable.'
            can_checkout = False

        elif item.quantity > item.max_quantity:
            item.unavailable_reason = f'Only {item.max_quantity} item(s) available.'
            can_checkout = False

        else:
            item.unavailable_reason = ''

        if item.is_available:
            total_price += item.subtotal
            total_items += item.quantity

    return render(request, 'cart/cart.html', {
        'cart_items': cart_items,
        'total_price': total_price,
        'total_items': total_items,
        'can_checkout': can_checkout,
    })

@login_required(login_url='user_login')
def update_cart_quantity(request):
    if request.method != 'POST':
        return redirect('cart_page')

    item_id = request.POST.get('item_id', '').strip()
    action = request.POST.get('action', '').strip()

    cart_item = get_object_or_404(
        Cart.objects.select_related(
            'product',
            'variant',
            'product__subcategory',
            'product__subcategory__category'
        ),
        id=item_id,
        user=request.user
    )

    product_available = (
        cart_item.product.is_active and
        cart_item.product.subcategory is not None and
        cart_item.product.subcategory.category.is_blocked is False and
        cart_item.variant.status == 'ACTIVE'
    )

    if not product_available:
        messages.error(request, 'This product is no longer available.')
        return redirect('cart_page')

    max_allowed = min(cart_item.variant.stock, MAX_QUANTITY_PER_ORDER)

    if max_allowed <= 0:
        messages.error(request, 'This product is out of stock.')
        return redirect('cart_page')

    if action == 'increase':
        if cart_item.quantity < max_allowed:
            cart_item.quantity += 1
            cart_item.save()
        else:
            messages.error(request, f'Maximum quantity is {max_allowed}.')

    elif action == 'decrease':
        if cart_item.quantity > 1:
            cart_item.quantity -= 1
            cart_item.save()
        else:
            cart_item.delete()
            messages.success(request, 'Item removed from cart.')

    else:
        messages.error(request, 'Invalid action.')

    return redirect('cart_page')

@login_required(login_url='user_login')
def remove_cart_item(request):
    if request.method != 'POST':
        return redirect('cart_page')

    item_id = request.POST.get('item_id', '').strip()

    cart_item = get_object_or_404(
        Cart,
        id=item_id,
        user=request.user
    )

    cart_item.delete()
    messages.success(request, 'Item removed from cart.')

    return redirect('cart_page')

@login_required(login_url='user_login')
def buy_now(request):
    if request.method != 'POST':
        return redirect('product_list')

    variant_id = request.POST.get('variant_id', '').strip()
    quantity = request.POST.get('quantity', '1').strip()

    try:
        quantity = int(quantity)
    except ValueError:
        messages.error(request, 'Invalid quantity.')
        return redirect(request.META.get('HTTP_REFERER', 'product_list'))

    if quantity < 1:
        messages.error(request, 'Quantity must be at least 1.')
        return redirect(request.META.get('HTTP_REFERER', 'product_list'))

    variant = get_object_or_404(
        ProductVariant.objects.select_related(
            'product',
            'product__subcategory',
            'product__subcategory__category'
        ),
        id=variant_id,
        status='ACTIVE',
        product__is_active=True,
        product__subcategory__category__is_blocked=False
    )

    max_allowed = min(variant.stock, MAX_QUANTITY_PER_ORDER)

    if max_allowed <= 0:
        messages.error(request, 'This variant is out of stock.')
        return redirect('product_detail', product_id=variant.product.id)

    if quantity > max_allowed:
        messages.error(request, f'Only {max_allowed} item(s) available.')
        return redirect('product_detail', product_id=variant.product.id)

    request.session['buy_now'] = {
        'variant_id': str(variant.id),
        'quantity': quantity,
    }
    request.session.modified = True

    checkout_url = reverse('checkout_page')
    return redirect(f'{checkout_url}?source=buy_now')

@login_required(login_url='user_login')
def wishlist_page(request):
    request.session['wishlist_notification_count'] = 0
    request.session.modified = True

    wishlist_items = (
        Wishlist.objects
        .filter(user=request.user)
        .select_related(
            'product',
            'variant',
            'product__subcategory',
            'product__subcategory__category'
        )
        .prefetch_related('variant__images')
        .order_by('-created_at')
    )

    for item in wishlist_items:
        first_image = item.variant.images.first()
        item.thumbnail = first_image.image.url if first_image else None

        item.is_available = (
            item.product.is_active and
            item.product.subcategory is not None and
            item.product.subcategory.category.is_blocked is False and
            item.variant.status == 'ACTIVE' and
            item.variant.stock > 0
        )

        item.max_quantity = min(item.variant.stock, MAX_QUANTITY_PER_ORDER)

        if not item.is_available:
            item.unavailable_reason = 'This product is currently unavailable.'
        else:
            item.unavailable_reason = ''

        item.display_price = item.variant.discounted_price

    return render(request, 'wishlist/wishlist.html', {
        'wishlist_items': wishlist_items,
    })

@login_required(login_url='user_login')
def add_to_wishlist(request):
    if request.method != 'POST':
        return redirect('product_list')

    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

    variant_id = request.POST.get('variant_id', '').strip()

    if not variant_id:
        if is_ajax:
            return JsonResponse({
                'success': False,
                'message': 'Please select a product option.'
            }, status=400)

        messages.error(request, 'Please select a product option.')
        return redirect(request.META.get('HTTP_REFERER', 'product_list'))

    variant = get_object_or_404(
        ProductVariant.objects.select_related(
            'product',
            'product__subcategory',
            'product__subcategory__category'
        ),
        id=variant_id,
        status='ACTIVE',
        product__is_active=True,
        product__subcategory__isnull=False,
        product__subcategory__category__is_blocked=False
    )

    wishlist_item, created = Wishlist.objects.get_or_create(
        user=request.user,
        variant=variant,
        defaults={
            'product': variant.product
        }
    )

    if created:
        current_count = request.session.get('wishlist_notification_count', 0)
        request.session['wishlist_notification_count'] = current_count + 1
        request.session.modified = True

        message = 'Product added to wishlist.'
    else:
        message = 'Product is already in your wishlist.'

    if is_ajax:
        return JsonResponse({
            'success': True,
            'message': message,
            'created': created,
            'wishlist_notification_count': request.session.get('wishlist_notification_count', 0)
        })

    if created:
        messages.success(request, message)
    else:
        messages.info(request, message)

    return redirect(request.META.get('HTTP_REFERER', 'product_list'))

@login_required(login_url='user_login')
def remove_wishlist_item(request, item_id):
    if request.method != 'POST':
        return redirect('wishlist_page')

    wishlist_item = get_object_or_404(
        Wishlist,
        id=item_id,
        user=request.user
    )

    wishlist_item.delete()
    messages.success(request, 'Item removed from wishlist.')

    return redirect('wishlist_page')

@login_required(login_url='user_login')
def add_wishlist_to_cart(request, item_id):
    if request.method != 'POST':
        return redirect('wishlist_page')

    wishlist_item = get_object_or_404(
        Wishlist.objects.select_related(
            'product',
            'variant',
            'product__subcategory',
            'product__subcategory__category'
        ),
        id=item_id,
        user=request.user
    )

    product_available = (
        wishlist_item.product.is_active and
        wishlist_item.product.subcategory is not None and
        wishlist_item.product.subcategory.category.is_blocked is False and
        wishlist_item.variant.status == 'ACTIVE' and
        wishlist_item.variant.stock > 0
    )

    if not product_available:
        messages.error(request, 'This product is no longer available.')
        return redirect('wishlist_page')

    max_allowed = min(wishlist_item.variant.stock, MAX_QUANTITY_PER_ORDER)

    if max_allowed <= 0:
        messages.error(request, 'This product is out of stock.')
        return redirect('wishlist_page')

    cart_item, created = Cart.objects.get_or_create(
        user=request.user,
        variant=wishlist_item.variant,
        defaults={
            'product': wishlist_item.product,
            'quantity': 1
        }
    )

    if not created:
        if cart_item.quantity >= max_allowed:
            messages.error(
                request,
                f'You can only add up to {max_allowed} item(s) for this variant.'
            )
            return redirect('wishlist_page')

        cart_item.quantity += 1
        cart_item.save()

    wishlist_item.delete()

    messages.success(request, 'Product moved to cart.')
    return redirect('wishlist_page')

@login_required(login_url='user_login')
def add_all_wishlist_to_cart(request):
    if request.method != 'POST':
        return redirect('wishlist_page')

    wishlist_items = (
        Wishlist.objects
        .filter(user=request.user)
        .select_related(
            'product',
            'variant',
            'product__subcategory',
            'product__subcategory__category'
        )
    )

    moved_count = 0
    skipped_count = 0

    for item in wishlist_items:
        product_available = (
            item.product.is_active and
            item.product.subcategory is not None and
            item.product.subcategory.category.is_blocked is False and
            item.variant.status == 'ACTIVE' and
            item.variant.stock > 0
        )

        if not product_available:
            skipped_count += 1
            continue

        max_allowed = min(item.variant.stock, MAX_QUANTITY_PER_ORDER)

        if max_allowed <= 0:
            skipped_count += 1
            continue

        cart_item, created = Cart.objects.get_or_create(
            user=request.user,
            variant=item.variant,
            defaults={
                'product': item.product,
                'quantity': 1
            }
        )

        if not created:
            if cart_item.quantity >= max_allowed:
                skipped_count += 1
                continue

            cart_item.quantity += 1
            cart_item.save()

        item.delete()
        moved_count += 1

    if moved_count > 0:
        messages.success(request, f'{moved_count} item(s) moved to cart.')

    if skipped_count > 0:
        messages.error(request, f'{skipped_count} item(s) could not be moved.')

    if moved_count == 0 and skipped_count == 0:
        messages.info(request, 'Your wishlist is empty.')

    return redirect('wishlist_page')