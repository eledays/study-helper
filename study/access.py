"""Shared website and bot access policy, read fresh from the database."""
from .models import AccessPolicy, AllowedTelegramUser


def access_allowed(s, telegram_id):
    policy = s.get(AccessPolicy, 1)
    return not (policy and policy.whitelist_enabled) or s.get(AllowedTelegramUser, telegram_id) is not None
