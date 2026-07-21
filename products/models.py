import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255)
    description = models.CharField(max_length=500)

    image = models.ImageField(upload_to="category_images/", blank=True, null=True)

    is_blocked = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


class SubCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="subcategories"
    )

    name = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "SubCategories"

    def __str__(self):
        return self.name


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255)

    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    description = models.TextField()

    subcategory = models.ForeignKey(
        SubCategory, on_delete=models.SET_NULL, null=True, related_name="products"
    )

    product_details = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    SIZE_CHOICES = [
        ("S", "S"),
        ("M", "M"),
        ("L", "L"),
        ("XL", "XL"),
    ]

    STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("ACTIVE", "Active"),
        ("INACTIVE", "Inactive"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")

    sku = models.CharField(max_length=100, unique=True, blank=True, null=True)

    size = models.CharField(max_length=5, choices=SIZE_CHOICES, null=True, blank=True)

    color = models.CharField(max_length=100, blank=True)

    color_hex = models.CharField(max_length=7, blank=True)

    stock = models.PositiveIntegerField(default=0)

    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    discount = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="DRAFT")

    is_default = models.BooleanField(default=False)

    image_url = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def _get_active_offers(self):
        """
        Return all currently-active Offer objects that apply to this variant's
        product, at any level: PRODUCT, SUBCATEGORY, CATEGORY.
        """
        # Imported here (not at module level) to avoid a circular import with
        # discounts.models.
        from discounts.models import Offer

        today = timezone.now().date()

        filters = Q(offer_type="PRODUCT", product_id=self.product_id)

        if self.product.subcategory_id:
            filters |= Q(
                offer_type="SUBCATEGORY",
                subcategory_id=self.product.subcategory_id,
            )

            if self.product.subcategory.category_id:
                filters |= Q(
                    offer_type="CATEGORY",
                    category_id=self.product.subcategory.category_id,
                )

        return Offer.objects.filter(
            filters,
            is_active=True,
            start_date__lte=today,
            end_date__gte=today,
        )

    @property
    def savings_display(self):
        """
        Return the best (lowest-price) discount source among:
        - manual discount field (always PERCENTAGE)
        - any active PRODUCT / SUBCATEGORY / CATEGORY offer

        Dict shape: {'price': Decimal, 'type': 'PERCENTAGE'|'FLAT',
                     'amount': Decimal, 'source': Offer|None}
        """
        base_price = self.price

        manual_price = base_price - (base_price * self.discount / Decimal("100"))
        candidates = [
            {
                "price": manual_price,
                "type": "PERCENTAGE",
                "amount": self.discount,
                "source": None,
            }
        ]

        for offer in self._get_active_offers():
            candidates.append(
                {
                    "price": offer.apply_to(base_price),
                    "type": offer.discount_type,
                    "amount": offer.discount_value,
                    "source": offer,
                }
            )

        return min(candidates, key=lambda c: c["price"])

    @property
    def discounted_price(self):
        return self.savings_display["price"]

    @property
    def effective_discount_percent(self):
        if self.price <= 0:
            return Decimal("0.00")
        return ((self.price - self.discounted_price) / self.price) * Decimal("100")

    def __str__(self):
        return f"{self.product.name} | {self.size} | {self.color}"


class ProductImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="images"
    )

    image = models.ImageField(upload_to="product_images/")

    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for {self.product_variant}"


class Cart(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart_items"
    )

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="cart_items")

    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="cart_items"
    )


    quantity = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    @property
    def unit_price(self):
        return self.variant.discounted_price


class Wishlist(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wishlist_items"
    )

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="wishlist_items")

    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="wishlist_items"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "variant")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} - {self.product.name}"