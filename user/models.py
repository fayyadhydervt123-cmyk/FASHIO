import random
import string
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import RegexValidator
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        return self.create_user(email, password, **extra_fields)


def generate_referral_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


class User(AbstractUser):
    username = None

    email = models.EmailField(unique=True)
    fullname = models.CharField(max_length=255)

    referral_code = models.CharField(max_length=10, unique=True, blank=True, null=True)
    referred_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="referrals"
    )
    referral_reward_given = models.BooleanField(default=False)

    image = models.ImageField(upload_to="profile_images/", null=True, blank=True)

    phone_validator = RegexValidator(
        regex=r"^\d{10,15}$", message="Enter a valid phone number (digits only)."
    )
    phone = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        null=True,
        validators=[phone_validator],
    )

    otp = models.CharField(max_length=6, blank=True, null=True)
    otp_created_at = models.DateTimeField(blank=True, null=True)
    otp_attempts = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    auth_provider = models.CharField(
        max_length=20,
        default="email",  # 'email' = manual signup, 'google' = google login
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def save(self, *args, **kwargs):
        if not self.referral_code:
            code = generate_referral_code()
            while User.objects.filter(referral_code=code).exists():
                code = generate_referral_code()
            self.referral_code = code
        super().save(*args, **kwargs)


INDIAN_STATES = [
    ("AN", "Andaman and Nicobar Islands"),
    ("AP", "Andhra Pradesh"),
    ("AR", "Arunachal Pradesh"),
    ("AS", "Assam"),
    ("BR", "Bihar"),
    ("CH", "Chandigarh"),
    ("CG", "Chhattisgarh"),
    ("DN", "Dadra and Nagar Haveli and Daman and Diu"),
    ("DL", "Delhi"),
    ("GA", "Goa"),
    ("GJ", "Gujarat"),
    ("HR", "Haryana"),
    ("HP", "Himachal Pradesh"),
    ("JK", "Jammu and Kashmir"),
    ("JH", "Jharkhand"),
    ("KA", "Karnataka"),
    ("KL", "Kerala"),
    ("LA", "Ladakh"),
    ("LD", "Lakshadweep"),
    ("MP", "Madhya Pradesh"),
    ("MH", "Maharashtra"),
    ("MN", "Manipur"),
    ("ML", "Meghalaya"),
    ("MZ", "Mizoram"),
    ("NL", "Nagaland"),
    ("OD", "Odisha"),
    ("PY", "Puducherry"),
    ("PB", "Punjab"),
    ("RJ", "Rajasthan"),
    ("SK", "Sikkim"),
    ("TN", "Tamil Nadu"),
    ("TG", "Telangana"),
    ("TR", "Tripura"),
    ("UP", "Uttar Pradesh"),
    ("UT", "Uttarakhand"),
    ("WB", "West Bengal"),
]


class Address(models.Model):

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")

    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=15)

    line1 = models.TextField()
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=6)
    state = models.CharField(max_length=2, choices=INDIAN_STATES)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Wallet(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet"
    )
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email}'s Wallet - ₹{self.balance}"


class WalletTransaction(models.Model):
    TRANSACTION_TYPE = (
        ("CREDIT", "Credit (Money In)"),
        ("DEBIT", "Debit (Money Out)"),
    )

    TRANSACTION_PURPOSE = (
        ("REFUND", "Refund for Cancellation/Return"),
        ("PURCHASE", "Order Payment"),
        ("RECHARGE", "Wallet Recharge"),
        ("REFERRAL_BONUS", "Referral Bonus"),
    )

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")

    amount = models.DecimalField(max_digits=10, decimal_places=2)

    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPE)

    purpose = models.CharField(max_length=20, choices=TRANSACTION_PURPOSE)

    razorpay_payment_id = models.CharField(max_length=100, unique=True, null=True, blank=True)

    order_id = models.CharField(max_length=50, blank=True, null=True)

    description = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.transaction_type} - ₹{self.amount} ({self.purpose})"