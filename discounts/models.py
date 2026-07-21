import uuid
from decimal import Decimal

from django.db import models
from django.utils import timezone


class Offer(models.Model):
    OFFER_TYPE_CHOICES = [
        ("PRODUCT", "Product Based"),
        ("SUBCATEGORY", "Subcategory Based"),
        ("CATEGORY", "Category Based"),
    ]

    DISCOUNT_TYPE_CHOICES = [
        ("PERCENTAGE", "Percentage"),
        ("FLAT", "Flat Amount"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    title = models.CharField(max_length=255)

    offer_type = models.CharField(max_length=20, choices=OFFER_TYPE_CHOICES)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="offers",
    )

    subcategory = models.ForeignKey(
        "products.SubCategory",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="offers",
    )

    category = models.ForeignKey(
        "products.Category",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="offers",
    )

    start_date = models.DateField()
    end_date = models.DateField()

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def target_name(self):
        if self.offer_type == "PRODUCT" and self.product:
            return self.product.name
        if self.offer_type == "SUBCATEGORY" and self.subcategory:
            return self.subcategory.name
        if self.offer_type == "CATEGORY" and self.category:
            return self.category.name
        return "N/A"

    @property
    def computed_status(self):
        today = timezone.now().date()

        if not self.is_active:
            return "INACTIVE"
        if today < self.start_date:
            return "UPCOMING"
        if today > self.end_date:
            return "EXPIRED"
        return "ACTIVE"

    @property
    def display_discount(self):
        if self.discount_type == "PERCENTAGE":
            return f"{self.discount_value}%"
        return f"₹{self.discount_value}"

    def apply_to(self, base_price):
        """Return the discounted price for a given base price."""
        if self.discount_type == "PERCENTAGE":
            return base_price - (base_price * self.discount_value / Decimal("100"))
        result = base_price - self.discount_value
        return result if result > 0 else Decimal("0.00")


class Coupon(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ("PERCENTAGE", "Percentage"),
        ("FLAT", "Flat Amount"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    code = models.CharField(max_length=20, unique=True)
    description = models.CharField(max_length=255, blank=True)

    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Cap for PERCENTAGE discounts. Leave blank for no cap.",
    )
    min_order_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )

    usage_limit_global = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Total redemptions allowed. Leave blank for unlimited.",
    )
    usage_limit_per_user = models.PositiveIntegerField(default=1)
    times_used = models.PositiveIntegerField(default=0)

    start_date = models.DateField()
    end_date = models.DateField()
    

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        super().save(*args, **kwargs)

    @property
    def computed_status(self):
        today = timezone.now().date()

        if not self.is_active:
            return "INACTIVE"
        if today < self.start_date:
            return "UPCOMING"
        if today > self.end_date:
            return "EXPIRED"
        if (
            self.usage_limit_global is not None
            and self.times_used >= self.usage_limit_global
        ):
            return "EXHAUSTED"
        return "ACTIVE"

    @property
    def display_discount(self):
        if self.discount_type == "PERCENTAGE":
            suffix = (
                f" (up to ₹{self.max_discount_amount})"
                if self.max_discount_amount
                else ""
            )
            return f"{self.discount_value}%{suffix}"
        return f"₹{self.discount_value}"

    def apply_to(self, order_total):
        """Return the discount amount (not the discounted price) for a given order total."""
        if self.discount_type == "PERCENTAGE":
            discount = order_total * self.discount_value / Decimal("100")
            if self.max_discount_amount is not None:
                discount = min(discount, self.max_discount_amount)
        else:
            discount = self.discount_value

        return min(discount, order_total)


class CouponUsage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="usages")
    user = models.ForeignKey(
        "user.User", on_delete=models.CASCADE, related_name="coupon_usages"
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coupon_usage",
    )
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)

    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-used_at"]