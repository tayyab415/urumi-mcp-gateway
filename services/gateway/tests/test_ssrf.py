"""Addresses the gateway refuses to connect to, and the Host check on /mcp."""

import pytest

from gateway.upstreams import blocked_address


@pytest.mark.parametrize("address", [
    "169.254.169.254", "::ffff:169.254.169.254", "fd00:ec2::254", "100.100.100.200",
    "0.0.0.0", "224.0.0.1", "fe80::1", "127.0.0.1", "::ffff:127.0.0.1", "::1",
])
def test_blocked(address):
    assert blocked_address(address, allow_loopback=False) is not None


@pytest.mark.parametrize("address", ["10.43.0.12", "172.18.0.3", "192.168.1.5", "34.67.189.86"])
def test_allowed(address):
    assert blocked_address(address, allow_loopback=False) is None


def test_loopback_only_in_local_development():
    assert blocked_address("127.0.0.1", allow_loopback=True) is None
    assert blocked_address("::ffff:127.0.0.1", allow_loopback=True) is None
