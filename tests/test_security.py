"""单元测试：口令哈希与会话令牌。"""

from __future__ import annotations

import pytest

from app import security


class TestHashPassword:
    def test_hash_then_verify_succeeds(self):
        stored = security.hash_password("correct horse", iterations=1000)
        assert security.verify_password("correct horse", stored) is True

    def test_hash_is_salted(self):
        """同一口令两次哈希结果必须不同（随机盐）。"""
        first = security.hash_password("same-password", iterations=1000)
        second = security.hash_password("same-password", iterations=1000)
        assert first != second
        assert security.verify_password("same-password", first)
        assert security.verify_password("same-password", second)

    def test_hash_never_contains_plaintext(self):
        stored = security.hash_password("hunter2", iterations=1000)
        assert "hunter2" not in stored

    def test_stored_format_carries_algorithm_and_iterations(self):
        stored = security.hash_password("whatever", iterations=4321)
        algorithm, iterations, salt, digest = stored.split("$")
        assert algorithm == "pbkdf2_sha256"
        assert iterations == "4321"
        assert salt and digest

    def test_verify_rejects_wrong_password(self):
        stored = security.hash_password("right", iterations=1000)
        assert security.verify_password("wrong", stored) is False

    def test_verify_is_case_and_space_sensitive(self):
        stored = security.hash_password("Secret", iterations=1000)
        assert security.verify_password("secret", stored) is False
        assert security.verify_password("Secret ", stored) is False

    def test_supports_unicode_password(self):
        stored = security.hash_password("密码🔒123", iterations=1000)
        assert security.verify_password("密码🔒123", stored) is True
        assert security.verify_password("密码123", stored) is False

    @pytest.mark.parametrize(
        "broken",
        [
            "",
            "not-a-hash",
            "pbkdf2_sha256$1000$onlythree",
            "pbkdf2_sha256$1000$c2FsdA$extra$parts",
            "md5$1000$c2FsdA$aGFzaA",
            "pbkdf2_sha256$abc$c2FsdA$aGFzaA",
            "pbkdf2_sha256$0$c2FsdA$aGFzaA",
            "pbkdf2_sha256$1000$$aGFzaA",
            "pbkdf2_sha256$1000$c2FsdA$",
            "pbkdf2_sha256$1000$!!!$aGFzaA",
        ],
    )
    def test_verify_rejects_malformed_hash(self, broken):
        """损坏的哈希串一律返回 False，不能抛异常。"""
        assert security.verify_password("anything", broken) is False

    def test_verify_rejects_non_string_input(self):
        assert security.verify_password(None, "x") is False
        assert security.verify_password("x", None) is False

    def test_hash_rejects_empty_password(self):
        with pytest.raises(ValueError):
            security.hash_password("")

    def test_hash_rejects_non_positive_iterations(self):
        with pytest.raises(ValueError):
            security.hash_password("x", iterations=0)


class TestSessionToken:
    def test_tokens_are_unique(self):
        tokens = {security.new_session_token() for _ in range(200)}
        assert len(tokens) == 200

    def test_token_is_url_safe_and_long_enough(self):
        token = security.new_session_token()
        assert len(token) >= 32
        assert all(char.isalnum() or char in "-_" for char in token)


class TestDummyVerify:
    def test_always_false(self):
        assert security.dummy_verify("anything") is False
        assert security.dummy_verify("") is False
