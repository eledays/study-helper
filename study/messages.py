"""Editable Telegram copy, reloaded when the TOML file changes."""
from functools import lru_cache
from pathlib import Path
import tomllib
from html import escape

MESSAGES_PATH = Path(__file__).with_name('bot_messages.toml')


class UserMessageError(ValueError):
    """A validation error whose text is safe to show to the user."""


@lru_cache(maxsize=1)
def _load(content):
    return tomllib.loads(content.decode('utf-8'))


def _value(key):
    catalog = _load(MESSAGES_PATH.read_bytes())
    section, name = key.split('.', 1)
    return catalog[section][name]


def bot_text(key, **values):
    template = _value(key)
    if not isinstance(template, str):
        raise TypeError(f'{key} must be a text template')
    return template.format(**values)


def bot_texts(key):
    variants = _value(key)
    if not isinstance(variants, list) or not variants or not all(isinstance(item, str) for item in variants):
        raise TypeError(f'{key} must be a nonempty list of text variants')
    return list(variants)


def bot_html(key, **values):
    """Trusted HTML copy with escaped text substitutions."""
    return bot_text(key, **{name: escape(value) if isinstance(value, str) else value
                           for name, value in values.items()})
