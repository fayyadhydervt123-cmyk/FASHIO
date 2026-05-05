from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class NoAllauthMessagesAdapter(DefaultAccountAdapter):
    def add_message(self, *args, **kwargs):
        pass  # suppress allauth messages


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        user.auth_provider = 'google'
        user.save(update_fields=['auth_provider'])
        return user