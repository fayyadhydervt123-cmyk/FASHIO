from django.urls import path

from . import views

urlpatterns = [
    path("offers/", views.offer_list, name="offer_list"),
    path("offers/add/", views.add_offer, name="add_offer"),
    path("offers/<uuid:offer_id>/edit/", views.edit_offer, name="edit_offer"),
    path("offers/<uuid:offer_id>/delete/", views.delete_offer, name="delete_offer"),
    path("coupons/", views.coupon_list, name="coupon_list"),
    path("coupons/add/", views.add_coupon, name="add_coupon"),
    path("coupons/<uuid:coupon_id>/edit/", views.edit_coupon, name="edit_coupon"),
    path("coupons/<uuid:coupon_id>/delete/", views.delete_coupon, name="delete_coupon"),
]