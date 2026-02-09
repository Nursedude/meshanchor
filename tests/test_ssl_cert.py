"""Tests for SSL certificate generation utility."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.ssl_cert import (
    is_cert_installed,
    generate_localhost_cert,
    get_meshtasticd_ssl_config,
    SSL_DIR,
    CA_KEY,
    CA_CERT,
    SERVER_KEY,
    SERVER_CERT,
    SYSTEM_CA_LINK,
)


class TestIsCertInstalled:
    """Test certificate installation detection."""

    @patch('utils.ssl_cert.SYSTEM_CA_LINK')
    @patch('utils.ssl_cert.CA_CERT')
    @patch('utils.ssl_cert.SERVER_CERT')
    @patch('utils.ssl_cert.SERVER_KEY')
    def test_all_files_exist(self, mock_key, mock_cert, mock_ca, mock_link):
        """Returns True when all cert files exist."""
        for m in [mock_key, mock_cert, mock_ca, mock_link]:
            m.exists.return_value = True
        assert is_cert_installed() is True

    @patch('utils.ssl_cert.SYSTEM_CA_LINK')
    @patch('utils.ssl_cert.CA_CERT')
    @patch('utils.ssl_cert.SERVER_CERT')
    @patch('utils.ssl_cert.SERVER_KEY')
    def test_missing_server_key(self, mock_key, mock_cert, mock_ca, mock_link):
        """Returns False when server key is missing."""
        mock_key.exists.return_value = False
        mock_cert.exists.return_value = True
        mock_ca.exists.return_value = True
        mock_link.exists.return_value = True
        assert is_cert_installed() is False

    @patch('utils.ssl_cert.SYSTEM_CA_LINK')
    @patch('utils.ssl_cert.CA_CERT')
    @patch('utils.ssl_cert.SERVER_CERT')
    @patch('utils.ssl_cert.SERVER_KEY')
    def test_missing_ca_link(self, mock_key, mock_cert, mock_ca, mock_link):
        """Returns False when system CA link is missing."""
        mock_key.exists.return_value = True
        mock_cert.exists.return_value = True
        mock_ca.exists.return_value = True
        mock_link.exists.return_value = False
        assert is_cert_installed() is False


class TestGenerateLocalhostCert:
    """Test certificate generation."""

    @patch('os.geteuid', return_value=1000)
    def test_requires_root(self, mock_euid):
        """Fails when not running as root."""
        success, msg = generate_localhost_cert()
        assert success is False
        assert "Root" in msg or "root" in msg

    @patch('os.geteuid', return_value=0)
    @patch('os.chmod')
    @patch('shutil.copy2')
    @patch('subprocess.run')
    @patch.object(Path, 'mkdir')
    @patch.object(Path, 'write_text')
    @patch.object(Path, 'exists', return_value=True)
    @patch.object(Path, 'unlink')
    def test_generates_certs_as_root(
        self, mock_unlink, mock_exists, mock_write, mock_mkdir,
        mock_run, mock_copy, mock_chmod, mock_euid
    ):
        """Succeeds when running as root with openssl available."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        success, msg = generate_localhost_cert()
        assert success is True
        assert "certificate.pem" in msg or "generated" in msg.lower()

    @patch('os.geteuid', return_value=0)
    @patch('subprocess.run')
    @patch.object(Path, 'mkdir')
    def test_handles_openssl_failure(self, mock_mkdir, mock_run, mock_euid):
        """Reports error when openssl fails."""
        mock_run.return_value = MagicMock(returncode=1, stderr="openssl error")
        success, msg = generate_localhost_cert()
        assert success is False
        assert "Failed" in msg

    @patch('os.geteuid', return_value=0)
    @patch('subprocess.run', side_effect=subprocess.TimeoutExpired('openssl', 30))
    @patch.object(Path, 'mkdir')
    def test_handles_timeout(self, mock_mkdir, mock_run, mock_euid):
        """Handles subprocess timeout gracefully."""
        success, msg = generate_localhost_cert()
        assert success is False
        assert "timed out" in msg.lower()


class TestGetMeshtasticdSslConfig:
    """Test YAML config snippet generation."""

    def test_returns_yaml_snippet(self):
        """Returns properly formatted YAML with cert paths."""
        config = get_meshtasticd_ssl_config()
        assert "SSLCert:" in config
        assert "SSLKey:" in config
        assert "certificate.pem" in config
        assert "private_key.pem" in config


class TestCertPaths:
    """Verify certificate path constants are correct."""

    def test_ssl_dir(self):
        assert SSL_DIR == Path("/etc/meshtasticd/ssl")

    def test_server_key_path(self):
        assert SERVER_KEY == Path("/etc/meshtasticd/ssl/private_key.pem")

    def test_server_cert_path(self):
        assert SERVER_CERT == Path("/etc/meshtasticd/ssl/certificate.pem")

    def test_system_ca_link(self):
        assert SYSTEM_CA_LINK == Path("/usr/local/share/ca-certificates/meshforge-localhost-ca.crt")
