import hashlib
import hmac

from forge.security.api_keys import KEY_PREFIX, digest_secret, parse_credential


def test_credential_round_trip_shape() -> None:
    credential = f"{KEY_PREFIX}public-id_secret-with_underscores"

    assert parse_credential(credential) == ("public-id", "secret-with_underscores")


def test_malformed_credentials_are_rejected() -> None:
    assert parse_credential("wrong_prefix_public_secret") is None
    assert parse_credential(KEY_PREFIX) is None
    assert parse_credential(f"{KEY_PREFIX}public-only") is None


def test_secret_digest_is_keyed_by_the_server_pepper() -> None:
    expected = hmac.digest(b"pepper", b"secret", hashlib.sha256)

    assert digest_secret("secret", "pepper") == expected
    assert digest_secret("secret", "different-pepper") != expected
