"""Hash password & validasi input pengguna (murni Python, tanpa Streamlit)."""
import hashlib
import hmac
import os
import re

ITERASI = 200_000


def buat_hash(password: str) -> str:
    """Hash PBKDF2-SHA256 dengan salt acak. Format: pbkdf2$iterasi$salt$hash."""
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERASI)
    return f"pbkdf2${ITERASI}${salt.hex()}${h.hex()}"


def cek_hash(password: str, tersimpan: str) -> bool:
    try:
        skema, iterasi, salt, h = tersimpan.split("$")
        if skema != "pbkdf2":
            return False
        calon = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterasi))
        return hmac.compare_digest(calon.hex(), h)
    except Exception:
        return False


def validasi_username(u: str):
    """Mengembalikan pesan error, atau None jika valid."""
    if not re.fullmatch(r"[a-z0-9._-]{3,20}", u):
        return "Username 3-20 karakter: huruf kecil, angka, titik, strip, atau garis bawah."
    return None


def validasi_password(p: str):
    if len(p) < 8:
        return "Password minimal 8 karakter."
    if not (re.search(r"[A-Za-z]", p) and re.search(r"\d", p)):
        return "Password harus kombinasi huruf dan angka."
    return None
