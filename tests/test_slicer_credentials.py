"""Guard the automatic slicer credential renewal.

Bambu CRLs are valid for one month and the printer refuses app_cert_install
with an expired one, so without renewal signed control stops after a month.
Renewal must never install material that would make things worse: a CRL that
is forged, stale or older than the installed one, or a key that does not
belong to the certificate. Each test below pins one of those guarantees.

BAMBU_SRC points to the patched upstream checkout (set by build.sh).
"""

import base64
import datetime
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

MODULE = (
    Path(os.environ["BAMBU_SRC"])
    / "custom_components/bambu_lab/pybambu/slicer_credentials.py"
)
UTC = datetime.UTC
NOW = datetime.datetime(2026, 11, 15, tzinfo=UTC)


def _load():
    spec = importlib.util.spec_from_file_location("slicer_credentials", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["slicer_credentials"] = module
    spec.loader.exec_module(module)
    return module


sc = _load()


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(subject, issuer, key, issuer_key, ca):
    return (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(_name(issuer))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - datetime.timedelta(days=365))
        .not_valid_after(NOW + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(issuer_key, hashes.SHA256())
    )


def _pem(obj):
    return obj.public_bytes(serialization.Encoding.PEM).decode()


def _key_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


class Pki:
    """Root -> intermediate -> application leaf, like Bambu's chain."""

    def __init__(self):
        self.root_key, self.inter_key = _key(), _key()
        self.root = _cert("root", "root", self.root_key, self.root_key, True)
        self.inter = _cert("inter", "root", self.inter_key, self.root_key, True)
        self.new_leaf()

    def new_leaf(self):
        self.leaf_key = _key()
        self.leaf = _cert("app", "inter", self.leaf_key, self.inter_key, False)

    def chain(self):
        return _pem(self.leaf) + _pem(self.inter) + _pem(self.root)

    def crl(self, last_update, days=31, signer=None, revoke_leaf=False):
        builder = (
            x509.CertificateRevocationListBuilder()
            .issuer_name(self.inter.subject)
            .last_update(last_update)
            .next_update(last_update + datetime.timedelta(days=days))
        )
        if revoke_leaf:
            builder = builder.add_revoked_certificate(
                x509.RevokedCertificateBuilder()
                .serial_number(self.leaf.serial_number)
                .revocation_date(last_update)
                .build()
            )
        return _pem(builder.sign(signer or self.inter_key, hashes.SHA256()))


def _key_blob(key):
    """Encode a key the way the endpoint does (SKEY struct, custom AES-CTR)."""
    n = key.private_numbers()
    parts = [v.to_bytes(128, "big") for v in (n.p, n.q, n.dmp1, n.dmq1, n.iqmp)]
    header = b"SKEY"[::-1] + b"".join(
        v.to_bytes(4, "little") for v in (1, 2048, 128, 128, 128, 128, 128)
    )
    plaintext = header + b"".join(parts)
    nonce = os.urandom(12)
    ciphertext = sc._ctr_xor(nonce, plaintext)
    return base64.b64encode(
        nonce + bytes(16) + len(ciphertext).to_bytes(4, "little") + ciphertext
    ).decode()


@pytest.fixture
def pki():
    return Pki()


@pytest.fixture
def installed(pki):
    return sc.Credentials(
        cert=pki.chain(),
        key=_key_pem(pki.leaf_key),
        crl=pki.crl(NOW - datetime.timedelta(days=25)),
    )


def _response(pki, crl, key=None):
    doc = {"code": 0, "cert": pki.chain(), "crl": [crl]}
    if key is not None:
        doc["key"] = _key_blob(key)
    return doc


def test_key_blob_round_trip():
    key = _key()
    assert (
        sc.decrypt_key_blob(_key_blob(key)).private_numbers() == key.private_numbers()
    )


def test_key_blob_with_changed_format_is_rejected():
    blob = bytearray(base64.b64decode(_key_blob(_key())))
    blob[-1] ^= 0xFF
    blob[32] ^= 0xFF  # corrupts the magic after decryption
    with pytest.raises(sc.CredentialError):
        sc.decrypt_key_blob(base64.b64encode(bytes(blob)).decode())


def test_renewal_window(pki):
    crl = pki.crl(NOW - datetime.timedelta(days=10), days=31)  # 21 days left
    assert not sc.needs_renewal(crl, NOW), (
        "a month-old CRL must not hit the endpoint every 12 h"
    )
    crl = pki.crl(NOW - datetime.timedelta(days=25), days=31)  # 6 days left
    assert sc.needs_renewal(crl, NOW)


def test_newer_crl_replaces_only_the_crl(pki, installed):
    fresh = pki.crl(NOW - datetime.timedelta(hours=1))
    renewed = sc.renewed_credentials(_response(pki, fresh), installed, NOW)
    assert renewed.crl == fresh
    assert renewed.cert == installed.cert and renewed.key == installed.key, (
        "unchanged leaf keeps the installed key"
    )


def test_crl_not_newer_than_installed_changes_nothing(pki, installed):
    same_age = pki.crl(NOW - datetime.timedelta(days=25))
    assert sc.renewed_credentials(_response(pki, same_age), installed, NOW) is None


def test_crl_signed_by_foreign_key_is_rejected(pki, installed):
    forged = pki.crl(NOW - datetime.timedelta(hours=1), signer=_key())
    with pytest.raises(sc.CredentialError):
        sc.renewed_credentials(_response(pki, forged), installed, NOW)


def test_expired_crl_in_response_is_rejected(pki, installed):
    stale = pki.crl(NOW - datetime.timedelta(days=40), days=31)
    with pytest.raises(sc.CredentialError):
        sc.renewed_credentials(_response(pki, stale), installed, NOW)


def test_revoked_application_certificate_is_rejected(pki, installed):
    revoked = pki.crl(NOW - datetime.timedelta(hours=1), revoke_leaf=True)
    with pytest.raises(sc.CredentialError):
        sc.renewed_credentials(_response(pki, revoked), installed, NOW)


def test_rotated_leaf_installs_matching_new_key(pki, installed):
    pki.new_leaf()
    renewed = sc.renewed_credentials(
        _response(pki, pki.crl(NOW), key=pki.leaf_key), installed, NOW
    )
    assert renewed.cert == pki.chain()
    new_key = serialization.load_pem_private_key(renewed.key.encode(), None)
    assert new_key.private_numbers() == pki.leaf_key.private_numbers()


def test_rotated_leaf_with_foreign_key_is_rejected(pki, installed):
    pki.new_leaf()
    with pytest.raises(sc.CredentialError):
        sc.renewed_credentials(_response(pki, pki.crl(NOW), key=_key()), installed, NOW)


def test_endpoint_error_is_rejected(pki, installed):
    with pytest.raises(sc.CredentialError):
        sc.renewed_credentials({"code": 1, "message": "denied"}, installed, NOW)
