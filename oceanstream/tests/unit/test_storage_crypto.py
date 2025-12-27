"""Tests for storage credential encryption utilities."""
import pytest
from pathlib import Path
import tempfile
import shutil

from oceanstream.storage.crypto import (
    _get_machine_id,
    get_encryption_key,
    encrypt_credential,
    decrypt_credential,
    hash_credential,
)


def test_get_machine_id_returns_consistent_value():
    """Test that machine ID is consistent across multiple calls."""
    machine_id_1 = _get_machine_id()
    machine_id_2 = _get_machine_id()
    
    assert machine_id_1 == machine_id_2
    assert isinstance(machine_id_1, str)
    assert len(machine_id_1) > 0


def test_get_encryption_key_returns_bytes():
    """Test that encryption key is returned as bytes."""
    key = get_encryption_key()
    
    assert isinstance(key, bytes)
    assert len(key) > 0


def test_get_encryption_key_is_consistent():
    """Test that encryption key is consistent across calls."""
    key1 = get_encryption_key()
    key2 = get_encryption_key()
    
    assert key1 == key2


def test_encrypt_credential_returns_string():
    """Test that encrypted credential is a string."""
    plaintext = "my-secret-password"
    encrypted = encrypt_credential(plaintext)
    
    assert isinstance(encrypted, str)
    assert encrypted != plaintext
    assert len(encrypted) > len(plaintext)


def test_encrypt_decrypt_roundtrip():
    """Test that encryption and decryption are reversible."""
    original = "my-azure-connection-string"
    
    encrypted = encrypt_credential(original)
    decrypted = decrypt_credential(encrypted)
    
    assert decrypted == original


def test_encrypt_different_plaintexts_produce_different_ciphertexts():
    """Test that different plaintexts produce different encrypted values."""
    text1 = "password123"
    text2 = "password456"
    
    encrypted1 = encrypt_credential(text1)
    encrypted2 = encrypt_credential(text2)
    
    assert encrypted1 != encrypted2


def test_encrypt_empty_string():
    """Test encryption of empty string."""
    encrypted = encrypt_credential("")
    decrypted = decrypt_credential(encrypted)
    
    assert decrypted == ""


def test_encrypt_unicode_characters():
    """Test encryption of unicode characters."""
    original = "パスワード🔒"
    
    encrypted = encrypt_credential(original)
    decrypted = decrypt_credential(encrypted)
    
    assert decrypted == original


def test_decrypt_invalid_data_raises_error():
    """Test that decrypting invalid data raises an error."""
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        decrypt_credential("not-valid-encrypted-data")


def test_decrypt_empty_string_raises_error():
    """Test that decrypting empty string raises an error."""
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        decrypt_credential("")


def test_hash_credential_returns_hex_string():
    """Test that hash returns a hex string."""
    credential = "my-password"
    hashed = hash_credential(credential)
    
    assert isinstance(hashed, str)
    assert len(hashed) == 64  # SHA256 produces 64 hex characters
    # Verify it's valid hex
    int(hashed, 16)


def test_hash_credential_is_consistent():
    """Test that hashing same credential produces same hash."""
    credential = "my-password"
    
    hash1 = hash_credential(credential)
    hash2 = hash_credential(credential)
    
    assert hash1 == hash2


def test_hash_credential_different_inputs_produce_different_hashes():
    """Test that different credentials produce different hashes."""
    hash1 = hash_credential("password1")
    hash2 = hash_credential("password2")
    
    assert hash1 != hash2


def test_hash_credential_empty_string():
    """Test hashing empty string."""
    hashed = hash_credential("")
    
    assert isinstance(hashed, str)
    assert len(hashed) == 64


def test_encrypt_long_credential():
    """Test encryption of very long credential."""
    # Create a long connection string (common with Azure)
    long_credential = "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=" + "x" * 500 + ";EndpointSuffix=core.windows.net"
    
    encrypted = encrypt_credential(long_credential)
    decrypted = decrypt_credential(encrypted)
    
    assert decrypted == long_credential
    assert len(encrypted) > len(long_credential)
