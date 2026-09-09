from app import crypto


def test_encrypt_decrypt_round_trip():
    ciphertext = crypto.encrypt_password("hunter2")
    assert ciphertext != "hunter2"
    assert crypto.decrypt_password(ciphertext) == "hunter2"


def test_key_file_created_with_owner_only_permissions():
    crypto.encrypt_password("anything")
    assert crypto._KEY_PATH.exists()
    mode = crypto._KEY_PATH.stat().st_mode & 0o777
    assert mode == 0o600


def test_key_persists_across_fresh_fernet_instances():
    ciphertext = crypto.encrypt_password("same-key-please")
    crypto._fernet = None  # force re-reading the key from disk
    assert crypto.decrypt_password(ciphertext) == "same-key-please"
