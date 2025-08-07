#!/usr/bin/env python3
"""
NHS GP Connect FHIR Diagnostic Tool
===================================

This script tests NHS Spine GP Connect FHIR API connectivity with the correct
interaction ID: urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1

Features:
- Tests GP Connect structured record endpoints
- Comprehensive logging and error analysis
- Public IP disclosure for NHSE traceability
- SSL/TLS handshake validation
- Multiple path testing for GP Connect endpoints
"""

import http.client
import json
import os
import socket
import ssl
import urllib.request
import uuid
from datetime import datetime


def get_public_ip():
    """Get public IP address for NHSE traceability."""
    try:
        print("🌍 Retrieving public IP address...")
        ip = (
            urllib.request.urlopen("https://api.ipify.org", timeout=10)
            .read()
            .decode("utf-8")
        )
        print(f"✅ Public IP: {ip}")
        return ip
    except Exception as e:
        print(f"❌ Failed to get public IP: {e}")
        return "Unavailable"


def validate_certificates(cert_dir):
    """Validate all required certificates exist."""
    print("🔍 Validating certificate files...")

    client_cert = os.path.join(cert_dir, "client_cert.pem")
    client_key = os.path.join(cert_dir, "client_key.pem")
    ca_bundle = os.path.join(cert_dir, "nhs_bundle.pem")

    cert_files = [
        ("Client Certificate", client_cert),
        ("Client Key", client_key),
        ("CA Bundle", ca_bundle),
    ]

    all_valid = True
    for name, path in cert_files:
        if os.path.exists(path):
            size = os.path.getsize(path)
            print(f"✅ {name}: {path} ({size} bytes)")
        else:
            print(f"❌ {name}: {path} - FILE NOT FOUND")
            all_valid = False

    return all_valid, client_cert, client_key, ca_bundle


def test_dns_resolution(host):
    """Test DNS resolution for the target host."""
    print(f"🌐 Testing DNS resolution for {host}...")

    try:
        resolved_ip = socket.gethostbyname(host)
        print(f"✅ DNS Resolution: {host} → {resolved_ip}")
        return resolved_ip
    except Exception as e:
        print(f"❌ DNS resolution failed: {e}")
        return None


def create_ssl_context(ca_bundle, client_cert, client_key):
    """Create SSL context with mutual TLS configuration."""
    print("🔐 Creating SSL context for mutual TLS...")

    try:
        ssl_context = ssl.create_default_context(cafile=ca_bundle)

        # Prefer TLS 1.2+ for NHS Spine compatibility
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_context.load_cert_chain(certfile=client_cert, keyfile=client_key)

        print("✅ SSL Context created successfully")
        print(f"   - Client Certificate: {client_cert}")
        print(f"   - Client Key: {client_key}")
        print(f"   - CA Bundle: {ca_bundle}")

        return ssl_context

    except Exception as e:
        print(f"❌ SSL Context creation failed: {e}")
        return None


def create_gpconnect_headers(asid, mhs_party_key, ods_code, trace_id):
    """Create NHS Spine GP Connect headers."""
    headers = {
        "Ssp-From": asid,
        "Ssp-To": mhs_party_key,
        "Ssp-InteractionID": "urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1",
        "Ssp-TraceID": trace_id,
        "Ssp-UserID": ods_code,
        "Accept": "application/fhir+json",
        "User-Agent": "Xhuma-GPConnect-Test/1.0",
        "Connection": "close",
    }

    print("📋 GP Connect Headers:")
    for key, value in headers.items():
        print(f"   {key}: {value}")

    return headers


def test_gpconnect_path(host, port, ssl_context, path, headers, nhs_number):
    """Test a single GP Connect path."""
    print(f"\n🔍 Testing GP Connect path: {path}")

    try:
        # Log raw request details
        print(f"📤 HTTP Request:")
        print(f"   Method: GET")
        print(f"   URL: https://{host}:{port}{path}")
        print(f"   Trace ID: {headers['Ssp-TraceID']}")

        # Establish connection
        conn = http.client.HTTPSConnection(host, port, context=ssl_context, timeout=30)

        # Send request
        request_time = datetime.now()
        conn.request("GET", path, None, headers)
        response = conn.getresponse()
        response_time = datetime.now()

        # Read response
        response_body = response.read().decode("utf-8", errors="ignore")
        response_headers = dict(response.getheaders())

        conn.close()

        # Log response details
        duration = (response_time - request_time).total_seconds()
        print(f"📥 HTTP Response:")
        print(f"   Status: {response.status} {response.reason}")
        print(f"   Duration: {duration:.3f}s")

        # Show key response headers
        content_type = response_headers.get("Content-Type", "N/A")
        content_length = response_headers.get("Content-Length", "N/A")
        server = response_headers.get("Server", "N/A")

        print(f"   Content-Type: {content_type}")
        print(f"   Content-Length: {content_length}")
        print(f"   Server: {server}")

        # Show response body (first 300 chars)
        if response_body:
            body_preview = response_body[:300]
            if len(response_body) > 300:
                body_preview += "..."
            print(f"   Response Body (first 300 chars): {body_preview}")
        else:
            print("   Response Body: (empty)")

        # Flag response status
        if response.status == 200:
            print("   🎉 SUCCESS: Got 200 response!")
        elif response.status == 403:
            print("   ⚠️  403 Forbidden - Authentication/Authorization issue")
        elif response.status == 404:
            print("   ⚠️  404 Not Found - Endpoint or resource not found")
        elif response.status == 500:
            print("   ⚠️  500 Server Error - Internal server error")
        elif response.status >= 400:
            print(f"   ⚠️  {response.status} Error - Client/Server error")

        return {
            "path": path,
            "status_code": response.status,
            "reason": response.reason,
            "content_type": content_type,
            "content_length": content_length,
            "server": server,
            "body_preview": response_body[:300] if response_body else "",
            "duration_seconds": duration,
            "timestamp": request_time.isoformat(),
        }

    except Exception as e:
        print(f"   ❌ Connection error: {e}")
        return {
            "path": path,
            "error": str(e),
            "error_type": type(e).__name__,
            "timestamp": datetime.now().isoformat(),
        }


def main():
    """Main execution function."""
    print("=" * 80)
    print("🔧 NHS GP Connect FHIR Diagnostic Tool")
    print("=" * 80)

    # Configuration
    cert_dir = "cert_dir"
    host = "proxy.intspineservices.nhs.uk"
    port = 443
    asid = "200000002574"
    ods_code = "RRV00"
    mhs_party_key = "RRV00-824607"
    nhs_number = "9690947714"

    # GP Connect structured record paths to test
    test_paths = [
        f"/{nhs_number}/structured",
        f"/structured/{nhs_number}",
        f"/GPConnect/StructuredRecord/{nhs_number}",
        f"/{mhs_party_key}/structured/{nhs_number}",
        f"/gpconnect/structured/{nhs_number}",
        f"/fhir/Patient/{nhs_number}/$gpc.getstructuredrecord",
        f"/FHIR/STU3/Patient/{nhs_number}/$gpc.getstructuredrecord",
        f"/{asid}/gpconnect/structured/{nhs_number}",
        f"/{ods_code}/gpconnect/structured/{nhs_number}",
        f"/gpconnect/fhir/Patient/{nhs_number}/$gpc.getstructuredrecord",
    ]

    print(f"🎯 Target: GP Connect Structured Record")
    print(f"📡 Host: {host}")
    print(f"🔢 NHS Number: {nhs_number}")
    print(f"🏥 ASID: {asid}")
    print(f"🏢 ODS Code: {ods_code}")
    print(f"🔑 MHS Party Key: {mhs_party_key}")
    print()

    # Step 1: Get public IP
    public_ip = get_public_ip()
    print()

    # Step 2: Validate certificates
    certs_valid, client_cert, client_key, ca_bundle = validate_certificates(cert_dir)
    if not certs_valid:
        print("❌ Certificate validation failed. Exiting.")
        return False
    print()

    # Step 3: Test DNS resolution
    resolved_ip = test_dns_resolution(host)
    if not resolved_ip:
        print("❌ DNS resolution failed. Exiting.")
        return False
    print()

    # Step 4: Create SSL context
    ssl_context = create_ssl_context(ca_bundle, client_cert, client_key)
    if not ssl_context:
        print("❌ SSL context creation failed. Exiting.")
        return False
    print()

    # Step 5: Test GP Connect paths
    print("📡 Testing GP Connect Structured Record Endpoints")
    print("=" * 60)

    results = []
    for path in test_paths:
        trace_id = str(uuid.uuid4())
        headers = create_gpconnect_headers(asid, mhs_party_key, ods_code, trace_id)
        result = test_gpconnect_path(host, port, ssl_context, path, headers, nhs_number)
        results.append(result)
        print("-" * 40)

    # Step 6: Generate summary
    print("\n📝 FINAL SUMMARY")
    print("=" * 50)
    print(f"🌍 Public IP: {public_ip}")
    print(f"📜 Certificates: All loaded successfully")
    print(f"🌐 Hostname Resolution: {host} → {resolved_ip}")
    print(f"🔐 SSL Context: Created successfully")
    print(f"🤝 TLS Handshake: Mutual TLS configured")
    print(
        f"🎯 Interaction ID: urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1"
    )
    print()

    print("📊 Path Test Results:")
    for i, result in enumerate(results, 1):
        if "error" in result:
            print(f"   {i}. {result['path']} - ERROR: {result['error']}")
        else:
            status = result["status_code"]
            reason = result["reason"]
            print(f"   {i}. {result['path']} - {status} {reason}")

    print()
    print("🔍 For NHSE Support:")
    print(f"   - Source IP: {public_ip}")
    print(f"   - Target: {host} ({resolved_ip})")
    print(f"   - Interaction: GP Connect Structured Record")
    print(f"   - ASID: {asid}")
    print(f"   - All trace IDs logged above for correlation")

    # Check if any requests succeeded
    success_count = sum(
        1 for r in results if "status_code" in r and r["status_code"] == 200
    )
    if success_count > 0:
        print(f"\n✅ {success_count} successful request(s)!")
    else:
        print(
            f"\n⚠️  No successful requests - check ASID authorization and endpoint configuration"
        )

    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
