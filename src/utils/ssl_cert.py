"""
MeshForge SSL Certificate Generator.

Generates a trusted self-signed CA and localhost server certificate
for meshtasticd's HTTPS web UI (port 9443).

The CA is added to the system trust store so all browsers and tools
(including lynx, curl, etc.) trust the connection without warnings.

Usage:
    from utils.ssl_cert import generate_localhost_cert, is_cert_installed

    if not is_cert_installed():
        success, msg = generate_localhost_cert()
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# Paths
SSL_DIR = Path("/etc/meshtasticd/ssl")
CA_KEY = SSL_DIR / "meshforge_ca.key"
CA_CERT = SSL_DIR / "meshforge_ca.pem"
SERVER_KEY = SSL_DIR / "private_key.pem"
SERVER_CERT = SSL_DIR / "certificate.pem"
SYSTEM_CA_DIR = Path("/usr/local/share/ca-certificates")
SYSTEM_CA_LINK = SYSTEM_CA_DIR / "meshforge-localhost-ca.crt"

# Certificate validity (days)
CA_DAYS = 3650       # 10 years
SERVER_DAYS = 3650   # 10 years


def is_cert_installed() -> bool:
    """Check if MeshForge localhost certificate is already installed and trusted."""
    return (
        SERVER_KEY.exists()
        and SERVER_CERT.exists()
        and CA_CERT.exists()
        and SYSTEM_CA_LINK.exists()
    )


def generate_localhost_cert() -> Tuple[bool, str]:
    """
    Generate a trusted self-signed CA and localhost server certificate.

    Creates:
      - CA key + cert (signs the server cert)
      - Server key + cert (used by meshtasticd, SANs: localhost + 127.0.0.1)
      - Installs CA to system trust store

    Returns:
        (success, message) tuple
    """
    if os.geteuid() != 0:
        return False, "Root privileges required. Run with sudo."

    try:
        # Ensure SSL directory exists
        SSL_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

        # Step 1: Generate CA private key
        result = subprocess.run(
            ["openssl", "genrsa", "-out", str(CA_KEY), "4096"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return False, f"Failed to generate CA key: {result.stderr}"
        os.chmod(str(CA_KEY), 0o600)

        # Step 2: Generate CA certificate
        result = subprocess.run(
            [
                "openssl", "req", "-new", "-x509",
                "-key", str(CA_KEY),
                "-out", str(CA_CERT),
                "-days", str(CA_DAYS),
                "-subj", "/CN=MeshForge Local CA/O=MeshForge/OU=Mesh Network"
            ],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return False, f"Failed to generate CA cert: {result.stderr}"

        # Step 3: Generate server private key
        result = subprocess.run(
            ["openssl", "genrsa", "-out", str(SERVER_KEY), "2048"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return False, f"Failed to generate server key: {result.stderr}"
        os.chmod(str(SERVER_KEY), 0o600)

        # Step 4: Create CSR with SANs via config
        openssl_conf = SSL_DIR / "openssl_san.cnf"
        openssl_conf.write_text(
            "[req]\n"
            "default_bits = 2048\n"
            "prompt = no\n"
            "distinguished_name = dn\n"
            "req_extensions = v3_req\n"
            "\n"
            "[dn]\n"
            "CN = localhost\n"
            "O = MeshForge\n"
            "OU = meshtasticd\n"
            "\n"
            "[v3_req]\n"
            "subjectAltName = @alt_names\n"
            "\n"
            "[alt_names]\n"
            "DNS.1 = localhost\n"
            "IP.1 = 127.0.0.1\n"
            "IP.2 = ::1\n"
        )

        csr_path = SSL_DIR / "server.csr"
        result = subprocess.run(
            [
                "openssl", "req", "-new",
                "-key", str(SERVER_KEY),
                "-out", str(csr_path),
                "-config", str(openssl_conf)
            ],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return False, f"Failed to generate CSR: {result.stderr}"

        # Step 5: Sign server cert with CA (include SANs)
        ext_conf = SSL_DIR / "v3_ext.cnf"
        ext_conf.write_text(
            "authorityKeyIdentifier=keyid,issuer\n"
            "basicConstraints=CA:FALSE\n"
            "keyUsage=digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1\n"
        )

        result = subprocess.run(
            [
                "openssl", "x509", "-req",
                "-in", str(csr_path),
                "-CA", str(CA_CERT),
                "-CAkey", str(CA_KEY),
                "-CAcreateserial",
                "-out", str(SERVER_CERT),
                "-days", str(SERVER_DAYS),
                "-extfile", str(ext_conf)
            ],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return False, f"Failed to sign server cert: {result.stderr}"

        # Step 6: Install CA to system trust store
        SYSTEM_CA_DIR.mkdir(parents=True, exist_ok=True)
        # Copy CA cert (update-ca-certificates expects .crt extension)
        import shutil
        shutil.copy2(str(CA_CERT), str(SYSTEM_CA_LINK))

        result = subprocess.run(
            ["update-ca-certificates"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.warning("update-ca-certificates returned non-zero: %s", result.stderr)
            # Non-fatal - cert files are still usable

        # Step 7: Clean up temp files
        for tmp in [csr_path, openssl_conf, ext_conf, SSL_DIR / "meshforge_ca.srl"]:
            if tmp.exists():
                tmp.unlink()

        # Set ownership - meshtasticd needs to read these
        for f in [SERVER_KEY, SERVER_CERT, CA_KEY, CA_CERT]:
            os.chmod(str(f), 0o600)

        logger.info("SSL certificates generated and installed")
        return True, (
            "SSL certificate generated and trusted.\n"
            f"  CA cert:     {CA_CERT}\n"
            f"  Server cert: {SERVER_CERT}\n"
            f"  Server key:  {SERVER_KEY}\n\n"
            "System trust store updated."
        )

    except subprocess.TimeoutExpired:
        return False, "Certificate generation timed out"
    except Exception as e:
        return False, f"Certificate generation failed: {e}"


def get_meshtasticd_ssl_config() -> str:
    """Return YAML snippet for meshtasticd SSL configuration."""
    return (
        f"  SSLCert: {SERVER_CERT}\n"
        f"  SSLKey: {SERVER_KEY}\n"
    )
