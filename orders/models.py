from django.db import models
from django.conf import settings
# Create your models here.





class Order(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("COD", "Cash On Delivery"),
        ("WALLET", "Wallet"),
        ("RAZORPAY", "Razorpay"),
    ]

    PAYMENT_STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("PAID", "Paid"),
        ("FAILED", "Failed"),
    ]

    ORDER_STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("PLACED", "Placed"),
        ("SHIPPED", "Shipped"),
        ("OUT_FOR_DELIVERY", "Out For Delivery"),
        ("DELIVERED", "Delivered"),
        ("CANCELLED", "Cancelled"),
    ]

    order_id = models.CharField(max_length=30, unique=True, blank=True, null=True)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    address = models.ForeignKey("user.Address", on_delete=models.SET_NULL, null=True)

    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="COD")
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default="PENDING")
    order_status = models.CharField(max_length=30, choices=ORDER_STATUS_CHOICES, default="PENDING")

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class OrderItem(models.Model):
    ITEM_STATUS_CHOICES = [
        ("ACTIVE", "Active"),
        ("CANCELLED", "Cancelled"),
        ("RETURN_REQUESTED", "Return Requested"),
        ("RETURNED", "Returned"),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")

    product = models.ForeignKey("products.Product", on_delete=models.SET_NULL, null=True)
    variant = models.ForeignKey("products.ProductVariant", on_delete=models.SET_NULL, null=True)

    product_name = models.CharField(max_length=255)
    size = models.CharField(max_length=50, blank=True, null=True)
    color = models.CharField(max_length=100, blank=True, null=True)

    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)

    item_status = models.CharField(
        max_length=30,
        choices=ITEM_STATUS_CHOICES,
        default="ACTIVE"
    )

    cancel_reason = models.CharField(max_length=100, blank=True, null=True)
    cancel_comment = models.TextField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)

    return_reason = models.CharField(max_length=100, blank=True, null=True)
    return_comment = models.TextField(blank=True, null=True)
    return_requested_at = models.DateTimeField(blank=True, null=True)

  
    
class Payment(models.Model):
    PAYMENT_STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("PAID", "Paid"),
        ("FAILED", "Failed"),
    ]

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="payment")

    payment_method = models.CharField(max_length=20, default="COD")
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default="PENDING")

    transaction_id = models.CharField(max_length=255, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

   
class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    status = models.CharField(max_length=20)
    note = models.CharField(max_length=255, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

   