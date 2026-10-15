"""Exercise certificate packaging with synthetic credentials and no NHS network calls."""

import json
import stat
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from scripts import generate_nhs_client_files as generator


def make_certificate(subject, key, issuer, issuer_key, *, ca=False, expired=False, client_auth=True):
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=30))
        .not_valid_after(now + timedelta(days=-1 if expired else 30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=not ca,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
    )
    if not ca:
        usage = ExtendedKeyUsageOID.CLIENT_AUTH if client_auth else ExtendedKeyUsageOID.SERVER_AUTH
        builder = builder.add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


@pytest.fixture(scope="module")
def hierarchy():
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    intermediate_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NHS Root Authority G2")])
    root = make_certificate("NHS Root Authority G2", root_key, root_name, root_key, ca=True)
    intermediate = make_certificate("NHS Authentication G2", intermediate_key, root.subject, root_key, ca=True)
    leaf = make_certificate("GPC-TEST.example.nhs.uk", client_key, intermediate.subject, intermediate_key)
    return {
        "root_key": root_key,
        "intermediate_key": intermediate_key,
        "client_key": client_key,
        "root": root,
        "intermediate": intermediate,
        "leaf": leaf,
    }


@pytest.fixture(autouse=True)
def offline_authorities(monkeypatch, hierarchy):
    def download(environment):
        assert environment == "production"
        return hierarchy["intermediate"], hierarchy["root"]

    monkeypatch.setattr(generator, "download_authorities", download)


def write_sources(directory, hierarchy, *, certificate=None, suffix=".crt", encoding=serialization.Encoding.PEM):
    key_path = directory / "GPC-TEST.example.nhs.uk.key"
    key_path.write_bytes(
        hierarchy["client_key"].private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    certificate_path = key_path.with_suffix(suffix)
    certificate_path.write_bytes((certificate or hierarchy["leaf"]).public_bytes(encoding))
    return key_path, certificate_path


def assert_output(output, key_path, hierarchy):
    generated_key = serialization.load_pem_private_key((output / "client_key.pem").read_bytes(), password=None)
    original_data = key_path.read_bytes()
    loader = (
        serialization.load_pem_private_key
        if original_data.startswith(b"-----BEGIN")
        else serialization.load_der_private_key
    )
    original_key = loader(original_data, password=None)
    assert generated_key.private_numbers() == original_key.private_numbers()
    assert x509.load_pem_x509_certificates((output / "client_cert.pem").read_bytes()) == [
        hierarchy["leaf"],
        hierarchy["intermediate"],
    ]
    assert x509.load_pem_x509_certificates((output / "nhs_bundle.pem").read_bytes()) == [
        hierarchy["intermediate"],
        hierarchy["root"],
    ]
    report = (output / "thumbprints.json").read_text()
    assert isinstance(json.loads(report), dict)
    normalized_report = report.upper().replace(":", "")
    for name in ("leaf", "intermediate", "root"):
        assert hierarchy[name].fingerprint(hashes.SHA256()).hex().upper() in normalized_report
    assert stat.S_IMODE(output.stat().st_mode) == 0o777
    for name in ("client_key.pem", "client_cert.pem", "nhs_bundle.pem", "thumbprints.json"):
        assert stat.S_IMODE((output / name).stat().st_mode) == 0o777


def test_packages_matching_client_credentials_and_chain(tmp_path, hierarchy):
    key_path, certificate_path = write_sources(tmp_path, hierarchy)
    originals = key_path.read_bytes(), certificate_path.read_bytes()

    output = generator.generate_client_files(key_path)

    assert output == tmp_path / key_path.stem
    assert_output(output, key_path, hierarchy)
    assert (key_path.read_bytes(), certificate_path.read_bytes()) == originals


def test_explicit_output_root_uses_key_name(tmp_path, hierarchy):
    key_path, certificate_path = write_sources(tmp_path, hierarchy)
    destination = tmp_path / "deployment" / "credentials"

    output = generator.generate_client_files(key_path, certificate_path, output_root=destination)

    assert output == destination / key_path.stem
    assert_output(output, key_path, hierarchy)


def test_accepts_der_encoded_private_key_without_changing_the_source(tmp_path, hierarchy):
    key_path, _ = write_sources(tmp_path, hierarchy)
    source = hierarchy["client_key"].private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    key_path.write_bytes(source)

    output = generator.generate_client_files(key_path)

    assert_output(output, key_path, hierarchy)
    assert key_path.read_bytes() == source


@pytest.mark.parametrize(
    ("suffix", "encoding"),
    [
        (".crt", serialization.Encoding.DER),
        (".cer", serialization.Encoding.PEM),
        (".pem", serialization.Encoding.PEM),
        (".txt", serialization.Encoding.PEM),
    ],
)
def test_autodetects_x509_formats_and_same_named_extensions(tmp_path, hierarchy, suffix, encoding):
    key_path, _ = write_sources(tmp_path, hierarchy, suffix=suffix, encoding=encoding)

    output = generator.generate_client_files(key_path)

    assert_output(output, key_path, hierarchy)


@pytest.mark.parametrize(
    ("suffix", "encoding"), [(".p7b", serialization.Encoding.DER), (".p7c", serialization.Encoding.PEM)]
)
def test_accepts_pkcs7_and_selects_the_matching_leaf(tmp_path, hierarchy, suffix, encoding):
    key_path, certificate_path = write_sources(tmp_path, hierarchy, suffix=suffix)
    certificate_path.write_bytes(
        pkcs7.serialize_certificates([hierarchy["root"], hierarchy["intermediate"], hierarchy["leaf"]], encoding)
    )

    output = generator.generate_client_files(key_path)

    assert_output(output, key_path, hierarchy)


def test_rejects_a_key_that_does_not_match_the_certificate(tmp_path, hierarchy):
    key_path, certificate_path = write_sources(tmp_path, hierarchy)
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path.write_bytes(
        wrong_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    originals = key_path.read_bytes(), certificate_path.read_bytes()

    with pytest.raises(ValueError):
        generator.generate_client_files(key_path)

    assert not (tmp_path / key_path.stem).exists()
    assert (key_path.read_bytes(), certificate_path.read_bytes()) == originals


@pytest.mark.parametrize("problem", ["wrong_issuer", "wrong_signature", "expired", "server_auth_only", "non_ca"])
def test_rejects_invalid_client_chain_before_writing_files(tmp_path, hierarchy, monkeypatch, problem):
    issuer_name = hierarchy["intermediate"].subject
    signing_key = hierarchy["intermediate_key"]
    if problem == "wrong_issuer":
        issuer_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NHS Authentication G2 INT")])
    elif problem == "wrong_signature":
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = make_certificate(
        "GPC-TEST.example.nhs.uk",
        hierarchy["client_key"],
        issuer_name,
        signing_key,
        expired=problem == "expired",
        client_auth=problem != "server_auth_only",
    )
    if problem == "non_ca":
        non_ca = make_certificate(
            "NHS Authentication G2",
            hierarchy["intermediate_key"],
            hierarchy["root"].subject,
            hierarchy["root_key"],
            ca=False,
        )
        monkeypatch.setattr(generator, "download_authorities", lambda environment: (non_ca, hierarchy["root"]))
    key_path, certificate_path = write_sources(tmp_path, hierarchy, certificate=leaf)
    originals = key_path.read_bytes(), certificate_path.read_bytes()

    with pytest.raises(ValueError):
        generator.generate_client_files(key_path)

    assert not (tmp_path / key_path.stem).exists()
    assert (key_path.read_bytes(), certificate_path.read_bytes()) == originals


def test_rejects_ambiguous_certificate_inputs(tmp_path, hierarchy):
    key_path, certificate_path = write_sources(tmp_path, hierarchy)
    key_path.with_suffix(".cer").write_bytes(certificate_path.read_bytes())

    with pytest.raises(ValueError):
        generator.generate_client_files(key_path)

    assert not (tmp_path / key_path.stem).exists()


@pytest.mark.parametrize("authority", ["root", "intermediate"])
def test_rejects_expired_downloaded_authorities(tmp_path, hierarchy, monkeypatch, authority):
    root = hierarchy["root"]
    intermediate = hierarchy["intermediate"]
    if authority == "root":
        root = make_certificate(
            "NHS Root Authority G2", hierarchy["root_key"], root.subject, hierarchy["root_key"], ca=True, expired=True
        )
    else:
        intermediate = make_certificate(
            "NHS Authentication G2",
            hierarchy["intermediate_key"],
            root.subject,
            hierarchy["root_key"],
            ca=True,
            expired=True,
        )
    monkeypatch.setattr(generator, "download_authorities", lambda environment: (intermediate, root))
    key_path, _ = write_sources(tmp_path, hierarchy)

    with pytest.raises(ValueError):
        generator.generate_client_files(key_path)

    assert not (tmp_path / key_path.stem).exists()


def test_explicit_certificate_resolves_ambiguous_inputs(tmp_path, hierarchy):
    key_path, certificate_path = write_sources(tmp_path, hierarchy)
    key_path.with_suffix(".cer").write_bytes(b"not the selected certificate")

    output = generator.generate_client_files(key_path, certificate_path)

    assert_output(output, key_path, hierarchy)


def test_rejects_explicit_certificate_with_a_different_name(tmp_path, hierarchy):
    key_path, certificate_path = write_sources(tmp_path, hierarchy)
    renamed = certificate_path.with_name("another-client.crt")
    certificate_path.rename(renamed)

    with pytest.raises(ValueError):
        generator.generate_client_files(key_path, renamed)

    assert not (tmp_path / key_path.stem).exists()


def test_preserves_existing_output_unless_overwrite_is_requested(tmp_path, hierarchy):
    key_path, _ = write_sources(tmp_path, hierarchy)
    output = tmp_path / key_path.stem
    output.mkdir()
    (output / "client_key.pem").write_bytes(b"existing credential")
    (output / "notes.txt").write_bytes(b"keep this unrelated file")

    with pytest.raises((ValueError, FileExistsError)):
        generator.generate_client_files(key_path)

    assert (output / "client_key.pem").read_bytes() == b"existing credential"
    assert set(path.name for path in output.iterdir()) == {"client_key.pem", "notes.txt"}

    assert generator.generate_client_files(key_path, overwrite=True) == output
    assert_output(output, key_path, hierarchy)
    assert (output / "notes.txt").read_bytes() == b"keep this unrelated file"


def test_invalid_certificate_preserves_existing_output_even_with_overwrite(tmp_path, hierarchy):
    key_path, certificate_path = write_sources(tmp_path, hierarchy)
    certificate_path.write_bytes(b"not a certificate")
    output = tmp_path / key_path.stem
    output.mkdir()
    originals = {"client_key.pem": b"old key", "client_cert.pem": b"old cert", "nhs_bundle.pem": b"old bundle"}
    for name, content in originals.items():
        (output / name).write_bytes(content)

    with pytest.raises(ValueError):
        generator.generate_client_files(key_path, overwrite=True)

    assert {path.name: path.read_bytes() for path in output.iterdir()} == originals


@pytest.mark.parametrize("source_name", ["client_cert", "client_key"])
def test_rejects_output_colliding_with_supplied_credentials_even_with_overwrite(
    tmp_path, hierarchy, monkeypatch, source_name
):
    source_directory = tmp_path / source_name
    source_directory.mkdir()
    key_path, certificate_path = write_sources(source_directory, hierarchy)
    key_suffix, certificate_suffix = (".key", ".pem") if source_name == "client_cert" else (".pem", ".crt")
    key_path = key_path.rename(source_directory / f"{source_name}{key_suffix}")
    certificate_path = certificate_path.rename(source_directory / f"{source_name}{certificate_suffix}")
    originals = {path.name: path.read_bytes() for path in source_directory.iterdir()}

    def unexpected_download(environment):
        pytest.fail("Output/source collisions must be rejected before downloading authorities")

    monkeypatch.setattr(generator, "download_authorities", unexpected_download)

    with pytest.raises(ValueError):
        generator.generate_client_files(key_path, certificate_path, output_root=tmp_path, overwrite=True)

    assert {path.name: path.read_bytes() for path in source_directory.iterdir()} == originals
