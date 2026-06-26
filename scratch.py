from cryptography import x509
from cryptography.hazmat.primitives import hashes
import binascii
import sys

try:
    with open("keys/ucl2 - new.cer", "r") as f:
        pem_data = f.read()
except FileNotFoundError:
    print("File not found.")
    sys.exit(1)

from app.security import fix_pem_formatting
fixed_pem = fix_pem_formatting(pem_data)

try:
    ca_certs = x509.load_pem_x509_certificates(fixed_pem.encode("utf-8"))
    print(f"Loaded {len(ca_certs)} certificates.")
    for i, cert in enumerate(ca_certs):
        fingerprint = cert.fingerprint(hashes.SHA1())
        hex_fp = binascii.hexlify(fingerprint).decode('utf-8')
        print(f"Certificate {i+1}:")
        print(f"  Subject: {cert.subject.rfc4514_string()}")
        print(f"  Thumbprint: {hex_fp}")
except Exception as e:
    print(f"Error parsing certificates: {e}")
