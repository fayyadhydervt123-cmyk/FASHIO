from django.urls import path
from . import views

urlpatterns = [
    path('categories',               views.view_categories,       name="categories"),
    path('categories/add',           views.add_category,          name="add_category"),
    path('category/edit',            views.edit_category,         name="edit_category"),
    path('categories/toggle-block/', views.toggle_category_block, name='toggle_category_block'),
    path('products',                 views.view_products,         name="products"),
    path('subcategories',            views.view_subcategories,    name='subcategories'),
    path('subcategories/add',        views.add_subcategory,       name='add_subcategory'),
    path('subcategories/edit',       views.edit_subcategory,      name='edit_subcategory'),
    path('subcategories/delete',     views.delete_subcategory,    name='delete_subcategory'),
    path('add-product',              views.add_product,           name='add_product'),
    path('products/edit/<uuid:product_id>/', views.edit_product,  name='edit_product'),
    path('products/toggle-block/',   views.toggle_product_block,  name='toggle_product_block'),
    path('add-variant/<uuid:product_id>/', views.add_variant,     name='add_variant'),
    path('variants/<uuid:variant_id>/edit/', views.edit_variant,  name='edit_variant'),
    path('variants/toggle-status/',  views.toggle_variant_status, name='toggle_variant_status'),
    path('products/<uuid:product_id>/variants/', views.product_variants, name='product_variants'),
    path('products-list',            views.product_list,          name="product_list"),
    path('products-detail/<uuid:product_id>/', views.product_detail, name='product_detail'),
    path('cart/',                    views.cart_page,             name='cart_page'),
    path('buy-now/',                 views.buy_now,               name='buy_now'),
    path('cart/add/',                views.add_to_cart,           name='add_to_cart'),
    path('cart/update/',             views.update_cart_quantity,  name='update_cart_quantity'),
    path('cart/remove/',             views.remove_cart_item,      name='remove_cart_item'),
    ]
