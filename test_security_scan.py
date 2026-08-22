#!/usr/bin/env python3
"""Tests for gated security scanning.

True positives, placeholder suppression, gate-off, and no outbound network.
Run: ./.venv/bin/python test_security_scan.py
"""
from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

import rga_search as rs
import security_scan as ss
from schemas import SearchHit, SecurityFinding


def test_flag_off_denies():
    try:
        ss.run_scan(enabled=False, allowed_paths=["/tmp"], use_gitleaks=False)
        assert False, "should deny"
    except rs.SearchDenied as e:
        assert "off" in str(e).lower()
    print("[PASS] security_scan flag off denies")


def test_true_positive_aws_and_ip():
    if not rs.rga_binary():
        print("[SKIP] true-positive scan needs rga")
        return
    d = tempfile.mkdtemp(prefix="scan_tp_")
    p = Path(d, "leak.txt")
    try:
        p.write_text(
            "key = AKIAABCDEFGHIJKLMNOP\n"
            "router = 192.0.2.44\n",
            encoding="utf-8",
        )
        findings, msg = ss.run_scan(
            enabled=True, allowed_paths=[d],
            use_gitleaks=False, use_detect_secrets=False,
        )
        kinds = {f.kind for f in findings}
        assert "aws_access_key" in kinds, (findings, msg)
        assert "ipv4_literal" in kinds, (findings, msg)
        report = ss.format_scan_report(findings, msg)
        assert "AKIA" in report or "192.0.2.44" in report
    finally:
        p.unlink()
        os.rmdir(d)
    print("[PASS] true-positive AWS key and IPv4")


def test_placeholder_suppressed():
    if not rs.rga_binary():
        print("[SKIP] placeholder scan needs rga")
        return
    d = tempfile.mkdtemp(prefix="scan_fp_")
    p = Path(d, "docs.txt")
    try:
        p.write_text(
            "Put YOUR_API_KEY here: AKIAEXAMPLEPLACEHOLDER\n"
            "example: changeme\n",
            encoding="utf-8",
        )
        findings, msg = ss.run_scan(
            enabled=True, allowed_paths=[d],
            use_gitleaks=False, use_detect_secrets=False,
        )
        # Placeholder markers must suppress; leftover non-placeholder IPs none.
        for f in findings:
            assert "placeholder" not in f.excerpt.lower()
            assert "changeme" not in f.excerpt.lower()
            assert "example" not in f.excerpt.lower() or f.kind != "aws_access_key"
        if not findings:
            assert msg == "no matching content found" or "no matching" in msg
    finally:
        p.unlink()
        os.rmdir(d)
    print("[PASS] placeholder values suppressed")


def test_parse_scan_arg():
    roots, err = ss.parse_scan_arg("")
    assert roots is None and err == ""
    roots, err = ss.parse_scan_arg("please")
    assert err == "usage" and roots is None
    roots, err = ss.parse_scan_arg("~/Desktop")
    assert roots == ["~/Desktop"] and err == ""
    roots, err = ss.parse_scan_arg("--help")
    assert err == "usage"
    print("[PASS] :scan extras that are not a path become usage, not a chat turn")


def test_loopback_ip_not_a_finding():
    assert ss._from_hit(SearchHit(path="/x", line=1, text="bind 127.0.0.1")) is None
    assert ss._from_hit(SearchHit(path="/x", line=1, text="listen 0.0.0.0:80")) is None
    hit = ss._from_hit(SearchHit(path="/x", line=1, text="router = 192.0.2.44"))
    assert hit is not None and hit.kind == "ipv4_literal"
    print("[PASS] loopback IPs are not findings; documentation IPs still are")


def test_report_caps_long_lists():
    findings = [
        SecurityFinding(path=f"/f{i}", line=1, kind="ipv4_literal", excerpt="192.0.2.1")
        for i in range(50)
    ]
    report = ss.format_scan_report(findings, "50 finding(s)", max_show=10)
    assert report.count("[ipv4_literal]") == 10
    assert "40 more" in report
    assert "sent to the model" in report
    print("[PASS] long scan reports cap and say findings stay off-model")


def test_no_outbound_network():
    """Scan must not open TCP connections (no live credential verification)."""
    opened = []
    real_connect = socket.socket.connect

    def _blocked(self, address):
        opened.append(address)
        raise OSError("network blocked by test")

    socket.socket.connect = _blocked  # type: ignore[method-assign]
    try:
        if not rs.rga_binary():
            # Gate-off path also must not network.
            try:
                ss.run_scan(enabled=False, allowed_paths=["/tmp"], use_gitleaks=False)
            except rs.SearchDenied:
                pass
        else:
            d = tempfile.mkdtemp(prefix="scan_net_")
            p = Path(d, "a.txt")
            p.write_text("hello 192.0.2.1\n", encoding="utf-8")
            try:
                ss.run_scan(
                    enabled=True, allowed_paths=[d],
                    use_gitleaks=False, use_detect_secrets=False,
                )
            finally:
                p.unlink()
                os.rmdir(d)
        assert opened == [], f"unexpected network connect: {opened}"
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
    print("[PASS] scan made no outbound socket.connect calls")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
