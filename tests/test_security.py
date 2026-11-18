from app.security import api_keys_match


def test_api_key_comparison() -> None:
    assert api_keys_match("same-secret", "same-secret")
    assert not api_keys_match("wrong", "same-secret")
