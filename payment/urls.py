from django.urls import path

from . import views

urlpatterns = [
    path("razorpay/success/", views.razorpay_payment_success, name="razorpay_payment_success"),
    path("razorpay/failure/", views.razorpay_payment_failure, name="razorpay_payment_failure"),
    path("order/<uuid:order_id>/retry-payment/", views.retry_payment, name="retry_payment"),
    path("failure/", views.payment_failure_page, name="payment_failure_page"),
]