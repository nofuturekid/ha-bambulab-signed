"""Guard the signed-command envelope the X1C accepted on real hardware.

X1C firmware 01.12.00.00 (cloud mode) executed signed M106 (fan) and M140
(bed) commands only in this exact form. Any upstream or patch change that
alters it would make the printer reject every control command with
84033543 and raise HMS 0500_0500_0001_0007, so these tests pin it.

BAMBU_SRC points to the patched upstream checkout (set by build.sh).
"""

import base64
import datetime
import importlib.util
import json
import logging
import os
import sys
import types
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

PYBAMBU = Path(os.environ["BAMBU_SRC"]) / "custom_components/bambu_lab/pybambu"


def _load_signing():
    # Load signing.py on its own: pybambu/__init__ pulls in MQTT and HA.
    pkg = types.ModuleType("pyb")
    pkg.__path__ = [str(PYBAMBU)]
    sys.modules["pyb"] = pkg
    const = types.ModuleType("pyb.const")
    const.LOGGER = logging.getLogger("test")
    sys.modules["pyb.const"] = const
    for name in ("device_cert", "signing"):
        spec = importlib.util.spec_from_file_location(
            f"pyb.{name}", PYBAMBU / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"pyb.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["pyb.signing"]


def _cert(subject, issuer, public_key, signing_key, ca):
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    return builder.sign(signing_key, hashes.SHA256())


@pytest.fixture(scope="module")
def setup():
    signing = _load_signing()
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    device_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca = _cert("test-ca", "test-ca", ca_key.public_key(), ca_key, True)
    leaf = _cert("test-app", "test-ca", app_key.public_key(), ca_key, False)
    chain = "".join(
        c.public_bytes(serialization.Encoding.PEM).decode() for c in (leaf, ca)
    )
    key_pem = app_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return {
        "signing": signing,
        "cert_id": signing.cert_id_from_pem(chain),
        "key": signing.load_signing_key(key_pem),
        "leaf": leaf,
        "device_key": device_key,
    }


def _gcode(line):
    return {"print": {"sequence_id": "0", "command": "gcode_line", "param": line}}


def _sign(setup, msg):
    return setup["signing"].maybe_sign(
        msg, setup["cert_id"], setup["key"], device_key=setup["device_key"].public_key()
    )


def test_envelope_is_byte_identical_compact_json(setup):
    out = _sign(setup, _gcode("M140 S40\n"))
    assert isinstance(out, str), (
        "the envelope must be published as the exact signed text"
    )
    env = json.loads(out)
    print_text = json.dumps(
        env["print"], separators=(",", ":"), sort_keys=True, ensure_ascii=False
    )
    header_text = json.dumps(
        env["header"], separators=(",", ":"), sort_keys=True, ensure_ascii=False
    )
    assert out == '{"header":' + header_text + ',"print":' + print_text + "}"


def test_signature_covers_print_section(setup):
    env = json.loads(_sign(setup, _gcode("M106 P3 S51\n")))
    signed = (
        '{"print":'
        + json.dumps(env["print"], separators=(",", ":"), sort_keys=True)
        + "}"
    ).encode()
    header = env["header"]
    assert header["payload_len"] == len(signed)
    assert header["sign_alg"] == "RSA_SHA256" and header["sign_ver"] == "v1.0"
    assert header["cert_id"] == setup["cert_id"]
    setup["leaf"].public_key().verify(
        base64.b64decode(header["sign_string"]),
        signed,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_param_is_encrypted_without_plaintext_copy(setup):
    env = json.loads(_sign(setup, _gcode("M104 S200\n")))
    assert "param" not in env["print"], (
        "a plaintext param next to param_enc was not accepted"
    )
    plain = setup["device_key"].decrypt(
        base64.b64decode(env["print"]["param_enc"]), padding.PKCS1v15()
    )
    assert plain == b"M104 S200\n"


def test_sequence_ids_count_up_from_accepted_range(setup):
    first = int(json.loads(_sign(setup, _gcode("M140 S0\n")))["print"]["sequence_id"])
    second = int(json.loads(_sign(setup, _gcode("M140 S0\n")))["print"]["sequence_id"])
    assert 20000 <= first < second < 2**31, (
        "the template id 0 was not used by the accepted commands"
    )


def test_unsignable_command_is_dropped_not_sent_unsigned(setup):
    signing = setup["signing"]
    assert (
        signing.maybe_sign(
            _gcode("M140 S40\n"), setup["cert_id"], setup["key"], device_key=None
        )
        is None
    )


def test_non_print_messages_pass_through_unchanged(setup):
    msg = {"security": {"sequence_id": "0", "command": "app_cert_install"}}
    assert _sign(setup, msg) is msg
