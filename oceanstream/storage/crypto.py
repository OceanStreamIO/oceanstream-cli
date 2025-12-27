"""Encryption utilities for secure storage of credentials.

This module provides functions for encrypting and decrypting sensitive
credentials (connection strings, access keys, etc.) using Fernet symmetric
encryption with machine-specific keys.

Security Model:
- Keys derived from machine identifier (MAC address or UUID)
- Credentials encrypted before storage
- Automatic decryption on load
- File permissions set to 600 (owner read/write only)

Note: This provides convenience encryption against casual browsing of
config files. For production deployments, consider using a dedicated
secrets manager (e.g., Azure Key Vault, AWS Secrets Manager).
"""

import hashlib
import json
import platform
import uuid
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet


def _get_machine_id() -> str:
    """Get a consistent machine identifier for key derivation.
    
    Uses MAC address if available, falls back to UUID stored in
    ~/.oceanstream/.machine_id file.
    
    Returns:
        Machine identifier string (hex format).
    """
    # Try to get MAC address first
    try:
        mac = uuid.getnode()
        # Check if it's a valid MAC (not a random fallback)
        if (mac >> 40) % 2 == 0:  # Check multicast bit
            return hex(mac)[2:]
    except Exception:
        pass
    
    # Fall back to stored UUID
    oceanstream_dir = Path.home() / ".oceanstream"
    machine_id_file = oceanstream_dir / ".machine_id"
    
    if machine_id_file.exists():
        return machine_id_file.read_text().strip()
    
    # Generate new UUID and store it
    oceanstream_dir.mkdir(parents=True, exist_ok=True)
    new_id = str(uuid.uuid4())
    machine_id_file.write_text(new_id)
    machine_id_file.chmod(0o600)
    
    return new_id


def _derive_key(machine_id: str, salt: str = "oceanstream-v1") -> bytes:
    """Derive encryption key from machine ID using PBKDF2.
    
    Args:
        machine_id: Machine identifier string.
        salt: Salt for key derivation (should be consistent).
        
    Returns:
        32-byte key suitable for Fernet encryption.
    """
    # Use PBKDF2 with 100k iterations for key stretching
    key_material = hashlib.pbkdf2_hmac(
        'sha256',
        machine_id.encode(),
        salt.encode(),
        100000,
        dklen=32,
    )
    
    # Fernet needs base64-encoded 32-byte key
    import base64
    return base64.urlsafe_b64encode(key_material)


def get_encryption_key() -> bytes:
    """Get the encryption key for this machine.
    
    This key is derived from the machine's unique identifier and should
    be consistent across program invocations on the same machine.
    
    Returns:
        Fernet encryption key (bytes).
    """
    machine_id = _get_machine_id()
    return _derive_key(machine_id)


def encrypt_credential(credential: str) -> str:
    """Encrypt a credential string.
    
    Args:
        credential: Plaintext credential (connection string, access key, etc.).
        
    Returns:
        Encrypted credential as base64-encoded string.
    """
    key = get_encryption_key()
    cipher = Fernet(key)
    encrypted_bytes = cipher.encrypt(credential.encode())
    return encrypted_bytes.decode()


def decrypt_credential(encrypted_credential: str) -> str:
    """Decrypt an encrypted credential.
    
    Args:
        encrypted_credential: Base64-encoded encrypted credential.
        
    Returns:
        Plaintext credential string.
        
    Raises:
        cryptography.fernet.InvalidToken: If decryption fails (wrong key or corrupted data).
    """
    key = get_encryption_key()
    cipher = Fernet(key)
    decrypted_bytes = cipher.decrypt(encrypted_credential.encode())
    return decrypted_bytes.decode()


def hash_credential(credential: str) -> str:
    """Create a SHA256 hash of a credential for comparison.
    
    Used to detect if a credential has changed without storing plaintext.
    
    Args:
        credential: Plaintext credential.
        
    Returns:
        Hex-encoded SHA256 hash.
    """
    return hashlib.sha256(credential.encode()).hexdigest()
