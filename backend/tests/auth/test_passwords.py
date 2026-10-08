"""AC #3: Argon2id hashing, rehash detection and timing equalisation."""

import statistics
import time
import unicodedata
from collections.abc import Callable

from pwdlib.hashers.argon2 import Argon2Hasher

from app.auth import passwords

PASSPHRASE = "violet otter juggles teacups"  # noqa: S105 (test value)


def test_hash_is_argon2id_with_library_defaults() -> None:
    hashed = passwords.hash_password(PASSPHRASE)
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert PASSPHRASE not in hashed
    assert passwords.hash_password(PASSPHRASE) != hashed  # salted


def test_verify() -> None:
    hashed = passwords.hash_password(PASSPHRASE)
    assert passwords.verify_password(PASSPHRASE, hashed)
    assert not passwords.verify_password(PASSPHRASE + "!", hashed)
    assert not passwords.verify_password("", hashed)
    assert not passwords.verify_password(PASSPHRASE, "not-a-hash")


def test_unicode_is_nfkc_normalised() -> None:
    composed = "café ﬁve ½ moons"  # é composed, ﬁ ligature, ½
    decomposed = unicodedata.normalize("NFD", composed)
    hashed = passwords.hash_password(composed)
    assert passwords.verify_password(decomposed, hashed)
    assert passwords.verify_password("café five 1⁄2 moons", hashed)  # noqa: RUF001 (NFKC of ½)


def test_needs_rehash_for_weaker_parameters_or_other_schemes() -> None:
    weak = Argon2Hasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSPHRASE)
    assert passwords.needs_rehash(weak)
    assert not passwords.needs_rehash(passwords.hash_password(PASSPHRASE))
    assert passwords.needs_rehash("$2b$12$abcdefghijklmnopqrstuuJ8gR4N1oZ3u8m2y6xZq1b7Wm8n1cG2")

    ok, new_hash = passwords.verify_and_rehash(PASSPHRASE, weak)
    assert ok
    assert new_hash is not None
    assert not passwords.needs_rehash(new_hash)
    assert passwords.verify_and_rehash(PASSPHRASE, new_hash) == (True, None)
    assert passwords.verify_and_rehash("wrong", weak) == (False, None)


def test_verify_dummy_takes_comparable_time() -> None:
    hashed = passwords.hash_password(PASSPHRASE)
    assert passwords.verify_dummy(PASSPHRASE) is False

    def timed(fn: Callable[[], bool]) -> float:
        start = time.perf_counter()
        fn()
        return time.perf_counter() - start

    def real() -> bool:
        return passwords.verify_password("wrong password", hashed)

    def dummy() -> bool:
        return passwords.verify_dummy("wrong password")

    for fn in (real, dummy):  # warm up both paths
        timed(fn)
    # Interleave the samples so CPU frequency drift affects both sides equally.
    real_s, dummy_s = [], []
    for _ in range(9):
        real_s.append(timed(real))
        dummy_s.append(timed(dummy))
    real_t, dummy_t = statistics.median(real_s), statistics.median(dummy_s)
    assert abs(dummy_t - real_t) / real_t < 0.3, (real_t, dummy_t)
