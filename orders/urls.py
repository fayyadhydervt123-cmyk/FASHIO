from django.urls import path

from . import views

urlpatterns = [
    path("checkout/", views.checkout_page, name="checkout_page"),
    path("payment-method/", views.payment_method, name="payment_method"),
    path("place-order/", views.place_order, name="place_order"),
    path("order-success/<uuid:order_id>/", views.order_success, name="order_success"),
    path("orders/", views.admin_order_list, name="admin_order_list"),
    path("orders/<uuid:order_id>/", views.admin_order_detail, name="admin_order_detail"),
    path(
        "orders/<uuid:order_id>/change-status/",
        views.admin_change_order_status,
        name="admin_change_order_status",
    ),
    path("inventory/", views.inventory_list, name="inventory_list"),
    path(
        "inventory/<uuid:product_id>/update-stock/",
        views.update_inventory_stock,
        name="update_inventory_stock",
    ),
    path("my-orders", views.user_orders, name="user_orders"),
    path("profile/orders/<uuid:order_id>/", views.user_order_detail, name="user_order_detail"),
    path(
        "profile/orders/<uuid:order_id>/cancel/select/",
        views.user_cancel_order_select,
        name="user_cancel_order_select",
    ),
    path(
        "profile/orders/<uuid:order_id>/cancel/",
        views.user_cancel_order_page,
        name="user_cancel_order_page",
    ),
    path(
        "profile/orders/<uuid:order_id>/cancel/confirm/",
        views.user_confirm_cancel_items,
        name="user_confirm_cancel_items",
    ),
    path(
        "profile/orders/<uuid:order_id>/return/select/",
        views.user_return_order_select,
        name="user_return_order_select",
    ),
    path(
        "profile/orders/<uuid:order_id>/return/",
        views.user_return_order_page,
        name="user_return_order_page",
    ),
    path(
        "profile/orders/<uuid:order_id>/return/confirm/",
        views.user_confirm_return_items,
        name="user_confirm_return_items",
    ),
    path(
        "profile/orders/<uuid:order_id>/invoice/",
        views.download_invoice,
        name="download_invoice",
    ),
    path("returns/", views.admin_return_list, name="admin_return_list"),
    path("returns/<uuid:return_id>/", views.admin_return_detail, name="admin_return_detail"),
    path(
        "returns/<uuid:return_id>/approve/",
        views.admin_approve_returns,
        name="admin_approve_returns",
    ),
    path(
        "returns/<uuid:return_id>/mark-refunded/",
        views.admin_mark_refunded,
        name="admin_mark_refunded",
    ),
    path(
        "returns/<uuid:return_id>/reject/",
        views.admin_reject_returns,
        name="admin_reject_returns",
    ),
    path("cart/coupon/apply/", views.apply_coupon, name="apply_coupon"),
    path("cart/coupon/remove/", views.remove_coupon, name="remove_coupon"),
]