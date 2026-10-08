"""AC #4: password policy (length, breached list, username)."""

from pathlib import Path

import pytest

from app.auth import policy
from app.auth.policy import BreachedListError, ReasonCode, validate
from app.auth.settings import DEFAULT_BREACHED_PASSWORDS_PATH, AuthSettings


@pytest.fixture
def small_list(tmp_path: Path) -> AuthSettings:
    path = tmp_path / "breached.txt"
    path.write_text("Summer2024!!xyz\nﬁrst-place-winner\n\nhunter2hunter2\n", encoding="utf-8")
    return AuthSettings(breached_passwords_path=path)


def test_bundled_list_is_loaded_once_and_complete() -> None:
    assert DEFAULT_BREACHED_PASSWORDS_PATH.is_file()
    assert policy.preload() > 90_000
    first = policy.load_breached_list(DEFAULT_BREACHED_PASSWORDS_PATH)
    assert policy.load_breached_list(DEFAULT_BREACHED_PASSWORDS_PATH) is first


@pytest.mark.parametrize(
    ("password", "username", "expected"),
    [
        ("password1234", "asha", [ReasonCode.BREACHED]),
        ("PASSWORD1234", "asha", [ReasonCode.BREACHED]),  # case-insensitive
        ("elevenchars", "asha", [ReasonCode.TOO_SHORT]),
        ("asharao-picks-mangoes", "AshaRao", [ReasonCode.CONTAINS_USERNAME]),
        ("violet otter juggles teacups", "asha", []),
        ("kbzq-wv", "kbzq", [ReasonCode.TOO_SHORT, ReasonCode.CONTAINS_USERNAME]),
        ("zzzzqqqqvvvv", "asha", []),  # no composition rules
        ("violet otter juggles teacups", "", []),  # empty username: no username check
        ("violet otter juggles teacups", "   ", []),
    ],
)
def test_bundled_policy(password: str, username: str, expected: list[ReasonCode]) -> None:
    assert validate(password, username) == expected


def test_length_counts_code_points_after_nfkc() -> None:
    # 12 characters of 2-4 UTF-8 bytes each: long enough.
    assert validate("ñandú-ölçü-ß", "kbzq") == []
    # "ﬁ" (one ligature) becomes "fi": 11 typed characters, 12 after NFKC.
    assert validate("ﬁvehundredx", "kbzq") == []
    assert validate("éééééééééé", "kbzq") == [ReasonCode.TOO_SHORT]


def test_username_match_is_unicode_aware() -> None:
    # Fullwidth letters fold to ASCII under NFKC.
    fullwidth = "the-ＡＳＨＡ-rao-passphrase"  # noqa: RUF001
    assert validate(fullwidth, "asha") == [ReasonCode.CONTAINS_USERNAME]


def test_too_long(small_list: AuthSettings) -> None:
    settings = small_list.model_copy(update={"password_max_length": 64})
    assert validate("x" * 65, "asha", settings=settings) == [ReasonCode.TOO_LONG]
    assert validate("x" * 64, "asha", settings=settings) == []


def test_list_override_via_settings(small_list: AuthSettings) -> None:
    assert validate("summer2024!!XYZ", "asha", settings=small_list) == [ReasonCode.BREACHED]
    # Entries are NFKC-folded too.
    assert validate("first-place-winner", "asha", settings=small_list) == [ReasonCode.BREACHED]
    # Not in the small list (though it is in the bundled one).
    assert validate("password1234", "asha", settings=small_list) == []


def test_min_length_setting(small_list: AuthSettings) -> None:
    settings = small_list.model_copy(update={"password_min_length": 16})
    assert validate("fifteen-chars-x", "asha", settings=settings) == [ReasonCode.TOO_SHORT]


def test_missing_list_fails_with_a_clear_error(tmp_path: Path) -> None:
    settings = AuthSettings(breached_passwords_path=tmp_path / "missing.txt")
    with pytest.raises(BreachedListError, match=r"not found: .*missing\.txt"):
        policy.preload(settings)
    with pytest.raises(BreachedListError):
        validate("violet otter juggles teacups", "asha", settings=settings)


def test_empty_or_binary_list_fails(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("\n\n", encoding="utf-8")
    with pytest.raises(BreachedListError, match="empty"):
        policy.preload(AuthSettings(breached_passwords_path=empty))
    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(BreachedListError, match="unreadable"):
        policy.preload(AuthSettings(breached_passwords_path=binary))
