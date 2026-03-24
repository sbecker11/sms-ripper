"""Tests for pydantic Settings helpers."""

from config import Settings


def test_expand_chat_path_non_string_passthrough():
    assert Settings.expand_chat_path(123) == 123
