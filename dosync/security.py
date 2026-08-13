"""
DoSync Security — Layer 2
=========================
PKI local + TLS/mTLS para el hub DoSync.

Arquitectura:
    - The hub host acts as a local Certificate Authority (CA)
    - El hub tiene su propio certificado firmado por la CA
    - Los adapters externos (gpio_adapter, adapters de terceros)
      reciben certificados firmados por la misma CA
    - mTLS verifica identidad en ambas direcciones

Archivos generados (en certs/):
    certs/
    ├── ca.key          — clave privada de la CA (nunca sale de la Pi)
    ├── ca.crt          — certificado de la CA (se distribuye a clientes)
    ├── hub.key         — clave privada del hub
    ├── hub.crt         — certificado del hub (firmado por CA)
    ├── hub.csr         — certificate signing request (intermedio)
    └── adapters/
        ├── gpio.key    — clave privada del gpio_adapter
        └── gpio.crt    — certificado del gpio_adapter (firmado por CA)

Quick start:
    # Generar PKI completa (primera vez)
    python3 -m dosync.security setup

    # Emitir certificado para un adapter nuevo
    python3 -m dosync.security issue --name gpio --ip 127.0.0.1

    # Verify the certificates are valid and have not expired
    python3 -m dosync.security verify

    # Ver info de un certificado
    python3 -m dosync.security info --cert certs/hub.crt

Integration with uvicorn (HTTPS):
    uvicorn server:app --ssl-keyfile certs/hub.key --ssl-certfile certs/hub.crt

Integration with uvicorn (mTLS — requires a client certificate):
    uvicorn server:app \\
        --ssl-keyfile certs/hub.key \\
        --ssl-certfile certs/hub.crt \\
        --ssl-ca-certs certs/ca.crt
"""

from __future__ import annotations

import datetime
import ipaddress
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("dosync.security")

# ── Configuration ─────────────────────────────────────────────────────────────

from .paths import certs_dir as _certs_dir
CERTS_DIR       = _certs_dir()
CA_KEY_PATH     = CERTS_DIR / "ca.key"
CA_CERT_PATH    = CERTS_DIR / "ca.crt"
HUB_KEY_PATH    = CERTS_DIR / "hub.key"
HUB_CERT_PATH   = CERTS_DIR / "hub.crt"
HUB_CSR_PATH    = CERTS_DIR / "hub.csr"
ADAPTERS_DIR    = CERTS_DIR / "adapters"

CA_VALIDITY_DAYS  = 3650   # 10 years
CERT_VALIDITY_DAYS = 365   # 1 year, renewed annually
KEY_SIZE          = 4096   # bits RSA

CA_SUBJECT = (
    "/C=AR/ST=Cordoba/L=Cordoba"
    "/O=DoSync Local PKI"
    "/OU=Certificate Authority"
    "/CN=DoSync CA"
)

HUB_SUBJECT = (
    "/C=AR/ST=Cordoba/L=Cordoba"
    "/O=DoSync Protocol"
    "/OU=Hub"
    "/CN=dosync-hub"
)


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class CertInfo:
    """Details of a certificate."""
    subject: str
    issuer: str
    not_before: str
    not_after: str
    serial: str
    is_ca: bool
    path: Path
    days_until_expiry: int

    @property
    def is_expired(self) -> bool:
        return self.days_until_expiry <= 0

    @property
    def is_expiring_soon(self) -> bool:
        return 0 < self.days_until_expiry <= 30


@dataclass
class PKIStatus:
    """Estado completo de la PKI."""
    ca_exists: bool = False
    hub_cert_exists: bool = False
    ca_info: Optional[CertInfo] = None
    hub_info: Optional[CertInfo] = None
    adapter_certs: list[CertInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        return self.ca_exists and self.hub_cert_exists and not self.errors


# ── Helpers internos ──────────────────────────────────────────────────────────

def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Ejecuta un comando openssl."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"openssl command failed:\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    return result


def _openssl_available() -> bool:
    result = subprocess.run(["openssl", "version"], capture_output=True)
    return result.returncode == 0


def _ensure_dirs() -> None:
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    ADAPTERS_DIR.mkdir(parents=True, exist_ok=True)


def _cert_info(cert_path: Path) -> Optional[CertInfo]:
    """Extract details from a PEM certificate."""
    if not cert_path.exists():
        return None
    try:
        result = _run([
            "openssl", "x509",
            "-in", str(cert_path),
            "-noout",
            "-subject", "-issuer", "-dates", "-serial",
            "-ext", "basicConstraints",
        ])
        lines = result.stdout.strip().splitlines()
        info = {}
        for line in lines:
            if "=" in line:
                k, _, v = line.partition("=")
                info[k.strip().lower().replace(" ", "_")] = v.strip()

        # Parse the expiry date
        not_after_str = info.get("notafter", "")
        try:
            not_after = datetime.datetime.strptime(
                not_after_str, "%b %d %H:%M:%S %Y %Z"
            ).replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            days_left = (not_after - now).days
        except ValueError:
            days_left = -1

        is_ca = "CA:TRUE" in result.stdout

        return CertInfo(
            subject=info.get("subject", ""),
            issuer=info.get("issuer", ""),
            not_before=info.get("notbefore", ""),
            not_after=not_after_str,
            serial=info.get("serial", ""),
            is_ca=is_ca,
            path=cert_path,
            days_until_expiry=days_left,
        )
    except Exception as e:
        log.warning("Could not parse cert %s: %s", cert_path, e)
        return None


# ── PKI Core ──────────────────────────────────────────────────────────────────

def generate_ca(force: bool = False) -> None:
    """
    Genera la CA local (Certificate Authority).
    Done ONCE. The private key never leaves the hub host.
    
    Args:
        force: when True, regenerate even if present — DESTROYS the existing PKI
    """
    _ensure_dirs()

    if CA_KEY_PATH.exists() and CA_CERT_PATH.exists() and not force:
        log.info("CA already exists at %s — skipping. Use force=True to regenerate.", CA_CERT_PATH)
        return

    if force:
        log.warning("Regenerating CA — all existing certificates will be invalidated!")

    log.info("Generating CA key (%d bits)...", KEY_SIZE)
    _run([
        "openssl", "genrsa",
        "-out", str(CA_KEY_PATH),
        str(KEY_SIZE),
    ])
    # Permisos restrictivos en la clave privada
    CA_KEY_PATH.chmod(0o600)

    log.info("Generating CA self-signed certificate (valid %d days)...", CA_VALIDITY_DAYS)
    # Config file completo — requerido por OpenSSL 3.0 en Debian/Raspberry Pi OS
    # The -subj + -extensions approach fails on OpenSSL 3.0 without -config
    ca_ext_file = CERTS_DIR / "ca_ext.cnf"
    ca_ext_file.write_text(
        "[req]\n"
        "default_bits = 4096\n"
        "prompt = no\n"
        "default_md = sha256\n"
        "distinguished_name = dn\n"
        "x509_extensions = v3_ca\n"
        "\n"
        "[dn]\n"
        "C = AR\n"
        "ST = Cordoba\n"
        "L = Cordoba\n"
        "O = DoSync Local PKI\n"
        "OU = Certificate Authority\n"
        "CN = DoSync CA\n"
        "\n"
        "[v3_ca]\n"
        "subjectKeyIdentifier = hash\n"
        "authorityKeyIdentifier = keyid:always,issuer\n"
        "basicConstraints = critical,CA:TRUE\n"
        "keyUsage = critical,digitalSignature,cRLSign,keyCertSign\n"
    )
    _run([
        "openssl", "req",
        "-new", "-x509",
        "-key", str(CA_KEY_PATH),
        "-out", str(CA_CERT_PATH),
        "-days", str(CA_VALIDITY_DAYS),
        "-config", str(ca_ext_file),
    ])
    ca_ext_file.unlink(missing_ok=True)

    log.info("CA generated successfully:")
    log.info("  Key:  %s", CA_KEY_PATH)
    log.info("  Cert: %s", CA_CERT_PATH)


def generate_hub_cert(
    hub_ip: str = "127.0.0.1",
    hub_hostname: str = "localhost",
    force: bool = False,
) -> None:
    """
    Generate the hub certificate, signed by the local CA.
    
    Args:
        hub_ip:       hub address, for the SAN
        hub_hostname: hub hostname, for the SAN
        force:        regenerar aunque ya exista
    """
    _ensure_dirs()

    if not CA_KEY_PATH.exists():
        raise RuntimeError("CA not found. Run generate_ca() first.")

    if HUB_CERT_PATH.exists() and not force:
        log.info("Hub cert already exists — skipping. Use force=True to regenerate.")
        return

    log.info("Generating hub key (%d bits)...", KEY_SIZE)
    _run([
        "openssl", "genrsa",
        "-out", str(HUB_KEY_PATH),
        str(KEY_SIZE),
    ])
    HUB_KEY_PATH.chmod(0o600)

    log.info("Generating hub CSR...")
    _run([
        "openssl", "req",
        "-new",
        "-key", str(HUB_KEY_PATH),
        "-out", str(HUB_CSR_PATH),
        "-subj", HUB_SUBJECT,
    ])

    # SAN config file completo — requerido por OpenSSL 3.0
    ext_file = CERTS_DIR / "hub_ext.cnf"
    ext_file.write_text(
        "[req]\n"
        "distinguished_name = dn\n"
        "req_extensions = v3_req\n"
        "prompt = no\n"
        "[dn]\n"
        "CN = dosync-hub\n"
        "[v3_req]\n"
        f"subjectAltName = IP:{hub_ip},IP:127.0.0.1,DNS:{hub_hostname},DNS:localhost\n"
        "keyUsage = digitalSignature,keyEncipherment\n"
        "extendedKeyUsage = serverAuth\n"
    )

    log.info("Signing hub certificate with CA (valid %d days)...", CERT_VALIDITY_DAYS)
    _run([
        "openssl", "x509",
        "-req",
        "-in", str(HUB_CSR_PATH),
        "-CA", str(CA_CERT_PATH),
        "-CAkey", str(CA_KEY_PATH),
        "-CAcreateserial",
        "-out", str(HUB_CERT_PATH),
        "-days", str(CERT_VALIDITY_DAYS),
        "-extfile", str(ext_file),
        "-extensions", "v3_req",
    ])

    # Limpiar archivos intermedios
    HUB_CSR_PATH.unlink(missing_ok=True)
    ext_file.unlink(missing_ok=True)

    log.info("Hub certificate generated:")
    log.info("  Key:  %s", HUB_KEY_PATH)
    log.info("  Cert: %s", HUB_CERT_PATH)


def issue_adapter_cert(
    name: str,
    adapter_ip: str = "127.0.0.1",
    force: bool = False,
) -> tuple[Path, Path]:
    """
    Issue a certificate for an external adapter.
    The adapter uses it to authenticate to the hub over mTLS.
    
    Args:
        name:       nombre del adapter (ej: "gpio", "shelly-01")
        adapter_ip: adapter address, for the SAN
        force:      regenerar aunque ya exista
    
    Returns:
        (key_path, cert_path)
    """
    _ensure_dirs()

    if not CA_KEY_PATH.exists():
        raise RuntimeError("CA not found. Run generate_ca() first.")

    key_path  = ADAPTERS_DIR / f"{name}.key"
    cert_path = ADAPTERS_DIR / f"{name}.crt"
    csr_path  = ADAPTERS_DIR / f"{name}.csr"

    if cert_path.exists() and not force:
        log.info("Cert for adapter '%s' already exists — skipping.", name)
        return key_path, cert_path

    subject = (
        f"/C=AR/ST=Cordoba/L=Cordoba"
        f"/O=DoSync Protocol"
        f"/OU=Adapter"
        f"/CN=dosync-adapter-{name}"
    )
    san = f"subjectAltName=IP:{adapter_ip},IP:127.0.0.1\nkeyUsage=digitalSignature\nextendedKeyUsage=clientAuth"
    ext_file = ADAPTERS_DIR / f"{name}_ext.cnf"
    ext_file.write_text(f"[v3_req]\n{san}\n")

    log.info("Generating key for adapter '%s'...", name)
    _run(["openssl", "genrsa", "-out", str(key_path), "2048"])
    key_path.chmod(0o600)

    log.info("Generating CSR for adapter '%s'...", name)
    _run(["openssl", "req", "-new", "-key", str(key_path), "-out", str(csr_path), "-subj", subject])

    log.info("Signing adapter cert with CA...")
    _run([
        "openssl", "x509", "-req",
        "-in", str(csr_path),
        "-CA", str(CA_CERT_PATH),
        "-CAkey", str(CA_KEY_PATH),
        "-CAcreateserial",
        "-out", str(cert_path),
        "-days", str(CERT_VALIDITY_DAYS),
        "-extfile", str(ext_file),
        "-extensions", "v3_req",
    ])

    csr_path.unlink(missing_ok=True)
    ext_file.unlink(missing_ok=True)

    log.info("Adapter cert issued: %s", cert_path)
    return key_path, cert_path


def verify_chain(cert_path: Path) -> bool:
    """Verify a certificate was signed by the local CA."""
    if not CA_CERT_PATH.exists():
        return False
    result = _run(
        ["openssl", "verify", "-CAfile", str(CA_CERT_PATH), str(cert_path)],
        check=False,
    )
    return result.returncode == 0


# ── Setup completo ────────────────────────────────────────────────────────────

def setup(
    hub_ip: str = "127.0.0.1",
    hub_hostname: str = "localhost",
    issue_gpio: bool = True,
    force: bool = False,
) -> PKIStatus:
    """
    Set up the complete DoSync PKI in a single call.
    
    Genera:
        1. CA local
        2. Certificado del hub
        3. Certificado para gpio_adapter (opcional)
    
    Args:
        hub_ip:      IP del hub en la red local
        hub_hostname: hostname del hub
        issue_gpio:  when True, also issue the GPIO adapter certificate
        force:       regenerar todo aunque ya exista
    
    Returns:
        PKIStatus describing the resulting PKI
    """
    if not _openssl_available():
        raise RuntimeError("openssl not found. Install with: apt-get install openssl")

    log.info("=== DoSync PKI Setup ===")
    generate_ca(force=force)
    generate_hub_cert(hub_ip=hub_ip, hub_hostname=hub_hostname, force=force)

    if issue_gpio:
        issue_adapter_cert("gpio", adapter_ip=hub_ip, force=force)

    status = get_status()
    if status.is_ready:
        log.info("PKI setup complete. Hub ready for TLS/mTLS.")
        log.info("")
        log.info("To start the hub with HTTPS:")
        log.info("  uvicorn server:app --ssl-keyfile %s --ssl-certfile %s", HUB_KEY_PATH, HUB_CERT_PATH)
        log.info("")
        log.info("To start with mTLS (requires client certs):")
        log.info("  uvicorn server:app --ssl-keyfile %s --ssl-certfile %s --ssl-ca-certs %s",
                 HUB_KEY_PATH, HUB_CERT_PATH, CA_CERT_PATH)
    else:
        log.error("PKI setup failed: %s", status.errors)

    return status


def get_status() -> PKIStatus:
    """Return the current PKI state."""
    status = PKIStatus()

    status.ca_exists = CA_CERT_PATH.exists()
    status.hub_cert_exists = HUB_CERT_PATH.exists()

    if status.ca_exists:
        status.ca_info = _cert_info(CA_CERT_PATH)

    if status.hub_cert_exists:
        status.hub_info = _cert_info(HUB_CERT_PATH)
        if status.hub_info and not verify_chain(HUB_CERT_PATH):
            status.errors.append("Hub cert is not signed by the local CA")

    # Certs de adapters
    if ADAPTERS_DIR.exists():
        for cert_file in ADAPTERS_DIR.glob("*.crt"):
            info = _cert_info(cert_file)
            if info:
                if not verify_chain(cert_file):
                    status.errors.append(f"Adapter cert {cert_file.name} not signed by local CA")
                if info.is_expired:
                    status.errors.append(f"Adapter cert {cert_file.name} has expired")
                elif info.is_expiring_soon:
                    log.warning("Adapter cert %s expires in %d days", cert_file.name, info.days_until_expiry)
                status.adapter_certs.append(info)

    if status.hub_info:
        if status.hub_info.is_expired:
            status.errors.append("Hub certificate has expired")
        elif status.hub_info.is_expiring_soon:
            log.warning("Hub cert expires in %d days — renew soon", status.hub_info.days_until_expiry)

    return status


def renew_hub_cert(hub_ip: str = "127.0.0.1", hub_hostname: str = "localhost") -> None:
    """Renew the hub certificate, keeping the same CA."""
    log.info("Renewing hub certificate...")
    generate_hub_cert(hub_ip=hub_ip, hub_hostname=hub_hostname, force=True)
    log.info("Hub certificate renewed. Restart the hub to apply.")


def renew_adapter_cert(name: str, adapter_ip: str = "127.0.0.1") -> tuple[Path, Path]:
    """Renew an adapter certificate."""
    log.info("Renewing cert for adapter '%s'...", name)
    return issue_adapter_cert(name=name, adapter_ip=adapter_ip, force=True)


# ── Automatic address detection ───────────────────────────────────────────────

def detect_hub_ip() -> str:
    """
    Try to detect the host's local network address.
    Falls back to 127.0.0.1 when it cannot be determined.
    """
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cmd_setup(args):
    hub_ip = args.ip or detect_hub_ip()
    print(f"Hub IP detected: {hub_ip}")
    status = setup(hub_ip=hub_ip, hub_hostname=args.hostname, force=args.force)
    _print_status(status)


def _cmd_issue(args):
    key, cert = issue_adapter_cert(args.name, adapter_ip=args.ip or "127.0.0.1", force=args.force)
    print(f"Issued cert for adapter '{args.name}':")
    print(f"  Key:  {key}")
    print(f"  Cert: {cert}")
    print(f"\nDistribute to the adapter process. The adapter must present this cert")
    print(f"when connecting to the hub (mTLS client authentication).")


def _cmd_verify(args):
    status = get_status()
    _print_status(status)
    if status.errors:
        sys.exit(1)


def _cmd_info(args):
    path = Path(args.cert)
    info = _cert_info(path)
    if not info:
        print(f"Could not read cert: {path}")
        sys.exit(1)
    print(f"Certificate: {path}")
    print(f"  Subject:        {info.subject}")
    print(f"  Issuer:         {info.issuer}")
    print(f"  Valid from:     {info.not_before}")
    print(f"  Valid until:    {info.not_after}")
    print(f"  Days remaining: {info.days_until_expiry}")
    print(f"  Is CA:          {info.is_ca}")
    chain_ok = verify_chain(path)
    print(f"  Chain valid:    {chain_ok}")


def _cmd_renew(args):
    if args.target == "hub":
        renew_hub_cert(hub_ip=detect_hub_ip())
        print("Hub certificate renewed. Restart the hub.")
    else:
        renew_adapter_cert(args.target)
        print(f"Adapter '{args.target}' certificate renewed.")


def _print_status(status: PKIStatus):
    print("\n=== DoSync PKI Status ===")
    print(f"  CA exists:         {'✓' if status.ca_exists else '✗'}")
    print(f"  Hub cert exists:   {'✓' if status.hub_cert_exists else '✗'}")
    if status.ca_info:
        print(f"  CA expires:        {status.ca_info.not_after} ({status.ca_info.days_until_expiry} days)")
    if status.hub_info:
        print(f"  Hub cert expires:  {status.hub_info.not_after} ({status.hub_info.days_until_expiry} days)")
    if status.adapter_certs:
        print(f"  Adapter certs:     {len(status.adapter_certs)}")
        for a in status.adapter_certs:
            exp = f"{a.days_until_expiry}d" if not a.is_expired else "EXPIRED"
            print(f"    - {a.path.stem:<20} {exp}")
    if status.errors:
        print(f"\n  Errors:")
        for e in status.errors:
            print(f"    ✗ {e}")
    else:
        print(f"\n  PKI is {'READY' if status.is_ready else 'INCOMPLETE'}")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="DoSync PKI management — Layer 2 security",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full setup (first time)
  python3 -m dosync.security setup

  # Setup with explicit IP
  python3 -m dosync.security setup --ip <hub-address>

  # Issue cert for a new adapter
  python3 -m dosync.security issue --name shelly-01 --ip 192.168.100.50

  # Verify PKI health
  python3 -m dosync.security verify

  # Show cert details
  python3 -m dosync.security info --cert certs/hub.crt

  # Renew hub cert
  python3 -m dosync.security renew hub

  # Renew adapter cert
  python3 -m dosync.security renew gpio
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Generate full PKI (CA + hub cert + gpio cert)")
    p_setup.add_argument("--ip", help="Hub IP address (auto-detected if omitted)")
    p_setup.add_argument("--hostname", default="dosync-hub", help="Hub hostname")
    p_setup.add_argument("--force", action="store_true", help="Regenerate even if exists")
    p_setup.set_defaults(func=_cmd_setup)

    p_issue = sub.add_parser("issue", help="Issue a cert for an adapter")
    p_issue.add_argument("--name", required=True, help="Adapter name (e.g. gpio, shelly-01)")
    p_issue.add_argument("--ip", help="Adapter IP address")
    p_issue.add_argument("--force", action="store_true", help="Regenerate even if exists")
    p_issue.set_defaults(func=_cmd_issue)

    p_verify = sub.add_parser("verify", help="Verify PKI health")
    p_verify.set_defaults(func=_cmd_verify)

    p_info = sub.add_parser("info", help="Show certificate details")
    p_info.add_argument("--cert", required=True, help="Path to .crt file")
    p_info.set_defaults(func=_cmd_info)

    p_renew = sub.add_parser("renew", help="Renew a certificate")
    p_renew.add_argument("target", help="'hub' or adapter name")
    p_renew.set_defaults(func=_cmd_renew)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
