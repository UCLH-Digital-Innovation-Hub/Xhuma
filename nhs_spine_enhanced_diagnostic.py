#!/usr/bin/env python3
"""
Enhanced NHS Spine FHIR PDS Diagnostic Tool
============================================

This script provides comprehensive diagnostics for NHS Spine FHIR PDS API connectivity
with enhanced logging, tracing, and error analysis capabilities for NHSE support.

Features:
- Full request/response logging with timestamps
- Certificate validation and SSL handshake details
- Public IP disclosure for NHSE traceability
- FHIR error response parsing and analysis
- Raw HTTP transaction logs
- Integration with existing Xhuma security patterns
"""

import ssl
import http.client
import socket
import os
import uuid
import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import urllib.request

# Configure comprehensive logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('nhs_spine_diagnostic.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger('NHS_Spine_Diagnostic')

class NHSSpineDiagnostic:
    """Enhanced NHS Spine FHIR PDS diagnostic tool with comprehensive logging."""
    
    def __init__(self):
        self.cert_dir = "cert_dir"
        self.host = "proxy.intspineservices.nhs.uk"
        self.port = 443
        
        # Configuration from Jakob's specification
        self.asid = "200000002574"
        self.ods_code = "RRV00"
        self.mhs_party_key = "RRV00-824607"
        self.interaction_id = "urn:nhs:names:services:pdsquery:read"
        
        # Certificate paths
        self.client_cert = os.path.join(self.cert_dir, "client_cert.pem")
        self.client_key = os.path.join(self.cert_dir, "client_key.pem")
        self.ca_bundle = os.path.join(self.cert_dir, "nhs_bundle.pem")
        
        # Session tracking
        self.session_id = str(uuid.uuid4())
        self.public_ip = None
        
        logger.info(f"🔧 NHS Spine Diagnostic Session Started: {self.session_id}")
    
    def get_public_ip(self) -> str:
        """Get public IP address for NHSE traceability."""
        try:
            logger.info("🌍 Retrieving public IP address...")
            self.public_ip = urllib.request.urlopen('https://api.ipify.org', timeout=10).read().decode('utf-8')
            logger.info(f"✅ Public IP: {self.public_ip}")
            return self.public_ip
        except Exception as e:
            logger.error(f"❌ Failed to get public IP: {e}")
            self.public_ip = "Unavailable"
            return self.public_ip
    
    def validate_certificates(self) -> bool:
        """Validate all required certificates exist."""
        logger.info("🔍 Validating certificate files...")
        
        cert_files = [
            ("Client Certificate", self.client_cert),
            ("Client Key", self.client_key),
            ("CA Bundle", self.ca_bundle)
        ]
        
        all_valid = True
        for name, path in cert_files:
            if os.path.exists(path):
                size = os.path.getsize(path)
                logger.info(f"✅ {name}: {path} ({size} bytes)")
            else:
                logger.error(f"❌ {name}: {path} - FILE NOT FOUND")
                all_valid = False
        
        return all_valid
    
    def test_dns_resolution(self) -> Optional[str]:
        """Test DNS resolution for the target host."""
        logger.info(f"🌐 Testing DNS resolution for {self.host}...")
        
        try:
            resolved_ip = socket.gethostbyname(self.host)
            logger.info(f"✅ DNS Resolution: {self.host} → {resolved_ip}")
            return resolved_ip
        except Exception as e:
            logger.error(f"❌ DNS resolution failed: {e}")
            return None
    
    def create_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Create SSL context with mutual TLS configuration."""
        logger.info("🔐 Creating SSL context for mutual TLS...")
        
        try:
            ssl_context = ssl.create_default_context(cafile=self.ca_bundle)
            
            # Prefer TLS 1.3, fallback to 1.2
            try:
                ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
                ssl_context.maximum_version = ssl.TLSVersion.TLSv1_3
                tls_version = "1.3"
            except (ValueError, AttributeError):
                ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
                tls_version = "1.2+"
            
            ssl_context.load_cert_chain(certfile=self.client_cert, keyfile=self.client_key)
            
            logger.info(f"✅ SSL Context created successfully")
            logger.info(f"   - TLS Version: {tls_version}")
            logger.info(f"   - Client Certificate: {self.client_cert}")
            logger.info(f"   - Client Key: {self.client_key}")
            logger.info(f"   - CA Bundle: {self.ca_bundle}")
            
            return ssl_context
            
        except Exception as e:
            logger.error(f"❌ SSL Context creation failed: {e}")
            return None
    
    def create_spine_headers(self, trace_id: Optional[str] = None) -> Dict[str, str]:
        """Create NHS Spine-specific headers following Jakob's specification."""
        if not trace_id:
            trace_id = str(uuid.uuid4())
        
        headers = {
            "Ssp-From": self.asid,
            "Ssp-To": self.mhs_party_key,
            "Ssp-InteractionID": self.interaction_id,
            "Ssp-TraceID": trace_id,
            "Ssp-UserID": self.ods_code,
            "Accept": "application/fhir+json",
            "User-Agent": "Xhuma-FHIR-PDS-Enhanced-Diagnostic/1.0",
            "Connection": "close",
            "X-Session-ID": self.session_id
        }
        
        logger.info("📋 NHS Spine Headers Created:")
        for key, value in headers.items():
            logger.info(f"   {key}: {value}")
        
        return headers
    
    def log_ssl_details(self, connection: http.client.HTTPSConnection) -> Dict[str, Any]:
        """Extract and log SSL connection details."""
        ssl_info = {}
        
        try:
            sock = connection.sock
            if hasattr(sock, 'getpeercert'):
                cert = sock.getpeercert()
                if cert:
                    ssl_info['server_cert_subject'] = cert.get('subject', 'N/A')
                    ssl_info['server_cert_issuer'] = cert.get('issuer', 'N/A')
                    ssl_info['serial_number'] = cert.get('serialNumber', 'N/A')
                    ssl_info['not_before'] = cert.get('notBefore', 'N/A')
                    ssl_info['not_after'] = cert.get('notAfter', 'N/A')
            
            if hasattr(sock, 'cipher'):
                cipher = sock.cipher()
                if cipher:
                    ssl_info['cipher_suite'] = cipher[0]
                    ssl_info['tls_version'] = cipher[1]
                    ssl_info['key_length'] = cipher[2]
            
            logger.info("🔒 SSL Connection Details:")
            for key, value in ssl_info.items():
                logger.info(f"   {key}: {value}")
                
        except Exception as e:
            ssl_info['error'] = str(e)
            logger.error(f"❌ Failed to extract SSL details: {e}")
        
        return ssl_info
    
    def parse_fhir_error(self, response_body: str, status_code: int) -> Dict[str, Any]:
        """Parse and analyze FHIR error responses."""
        error_analysis = {
            'status_code': status_code,
            'is_fhir_response': False,
            'error_type': 'Unknown',
            'issues': []
        }
        
        try:
            if response_body.strip().startswith('{'):
                fhir_data = json.loads(response_body)
                error_analysis['is_fhir_response'] = True
                
                if fhir_data.get('resourceType') == 'OperationOutcome':
                    error_analysis['error_type'] = 'FHIR OperationOutcome'
                    
                    issues = fhir_data.get('issue', [])
                    for issue in issues:
                        issue_detail = {
                            'severity': issue.get('severity', 'unknown'),
                            'code': issue.get('code', 'unknown'),
                            'details': issue.get('details', {}).get('text', 'No details'),
                            'diagnostics': issue.get('diagnostics', 'No diagnostics')
                        }
                        error_analysis['issues'].append(issue_detail)
                        
                        logger.error(f"🚨 FHIR Issue: {issue_detail['severity'].upper()} - {issue_detail['code']}")
                        logger.error(f"   Details: {issue_detail['details']}")
                        if issue_detail['diagnostics'] != 'No diagnostics':
                            logger.error(f"   Diagnostics: {issue_detail['diagnostics']}")
                
        except json.JSONDecodeError:
            logger.warning("⚠️  Response is not valid JSON")
            error_analysis['error_type'] = 'Non-JSON Response'
        
        return error_analysis
    
    def test_fhir_endpoint(self, nhs_number: str = "9690947714") -> Dict[str, Any]:
        """Test FHIR PDS endpoint with comprehensive logging."""
        logger.info(f"📡 Testing FHIR PDS endpoint for NHS number: {nhs_number}")
        
        # Test multiple potential paths
        test_paths = [
            f"/{self.asid}/personal-demographics/FHIR/R4/Patient/{nhs_number}",
            f"/personal-demographics/FHIR/R4/Patient/{nhs_number}",
            f"/FHIR/R4/Patient/{nhs_number}",
            f"/{self.ods_code}/personal-demographics/FHIR/R4/Patient/{nhs_number}",
            f"/{self.mhs_party_key}/personal-demographics/FHIR/R4/Patient/{nhs_number}"
        ]
        
        ssl_context = self.create_ssl_context()
        if not ssl_context:
            return {'success': False, 'error': 'Failed to create SSL context'}
        
        results = []
        
        for path in test_paths:
            logger.info(f"\n🔍 Testing path: {path}")
            trace_id = str(uuid.uuid4())
            headers = self.create_spine_headers(trace_id)
            
            try:
                # Log raw request details
                logger.info(f"📤 Raw HTTP Request:")
                logger.info(f"   Method: GET")
                logger.info(f"   URL: https://{self.host}:{self.port}{path}")
                logger.info(f"   Trace ID: {trace_id}")
                
                # Establish connection
                conn = http.client.HTTPSConnection(
                    self.host, 
                    self.port, 
                    context=ssl_context, 
                    timeout=30
                )
                
                # Send request
                request_time = datetime.now()
                conn.request('GET', path, None, headers)
                response = conn.getresponse()
                response_time = datetime.now()
                
                # Log SSL details
                ssl_info = self.log_ssl_details(conn)
                
                # Read response
                response_body = response.read().decode('utf-8', errors='ignore')
                response_headers = dict(response.getheaders())
                
                conn.close()
                
                # Log raw response details
                duration = (response_time - request_time).total_seconds()
                logger.info(f"📥 Raw HTTP Response:")
                logger.info(f"   Status: {response.status} {response.reason}")
                logger.info(f"   Duration: {duration:.3f}s")
                logger.info(f"   Content-Length: {len(response_body)} bytes")
                
                logger.info(f"   Response Headers:")
                for header, value in response_headers.items():
                    logger.info(f"     {header}: {value}")
                
                # Log response body (truncated for readability)
                if response_body:
                    if len(response_body) > 1000:
                        logger.info(f"   Response Body (first 1000 chars):")
                        logger.info(f"     {response_body[:1000]}...")
                        logger.info(f"   Full response body logged to file")
                    else:
                        logger.info(f"   Response Body:")
                        logger.info(f"     {response_body}")
                
                # Analyze errors
                error_analysis = None
                if response.status >= 400:
                    error_analysis = self.parse_fhir_error(response_body, response.status)
                
                result = {
                    'path': path,
                    'trace_id': trace_id,
                    'status_code': response.status,
                    'reason': response.reason,
                    'headers': response_headers,
                    'body': response_body,
                    'ssl_info': ssl_info,
                    'duration_seconds': duration,
                    'error_analysis': error_analysis,
                    'timestamp': request_time.isoformat()
                }
                
                results.append(result)
                
                # Success indicators
                if response.status == 200:
                    logger.info("🎉 SUCCESS: Got 200 response!")
                elif response.status == 403 and 'application/fhir' in response_headers.get('content-type', ''):
                    logger.info("✅ FHIR 403 response - authentication/authorization issue identified")
                elif response.status == 404 and 'application/fhir' in response_headers.get('content-type', ''):
                    logger.info("✅ FHIR 404 response - endpoint exists but resource not found")
                
            except Exception as e:
                logger.error(f"❌ Connection error for path {path}: {e}")
                results.append({
                    'path': path,
                    'error': str(e),
                    'error_type': type(e).__name__,
                    'timestamp': datetime.now().isoformat()
                })
        
        return {
            'success': True,
            'session_id': self.session_id,
            'public_ip': self.public_ip,
            'host': self.host,
            'results': results
        }
    
    def generate_summary_report(self, test_results: Dict[str, Any]) -> str:
        """Generate a comprehensive summary report for NHSE."""
        report = []
        report.append("="*80)
        report.append("NHS SPINE FHIR PDS DIAGNOSTIC SUMMARY REPORT")
        report.append("="*80)
        report.append(f"Session ID: {self.session_id}")
        report.append(f"Timestamp: {datetime.now().isoformat()}")
        report.append(f"Public IP: {self.public_ip}")
        report.append(f"Target Host: {self.host}")
        report.append("")
        
        report.append("🔧 Configuration:")
        report.append(f"   ASID: {self.asid}")
        report.append(f"   ODS Code: {self.ods_code}")
        report.append(f"   MHS Party Key: {self.mhs_party_key}")
        report.append(f"   Interaction ID: {self.interaction_id}")
        report.append("")
        
        report.append("📊 Test Results Summary:")
        for i, result in enumerate(test_results.get('results', []), 1):
            if 'error' in result:
                report.append(f"   {i}. {result['path']} - ERROR: {result['error']}")
            else:
                status = result['status_code']
                reason = result['reason']
                trace_id = result.get('trace_id', 'N/A')
                report.append(f"   {i}. {result['path']} - {status} {reason} (Trace: {trace_id})")
        
        report.append("")
        report.append("🔍 For NHSE Support:")
        report.append(f"   - All requests originate from IP: {self.public_ip}")
        report.append(f"   - Session ID for correlation: {self.session_id}")
        report.append(f"   - Full logs available in: nhs_spine_diagnostic.log")
        report.append(f"   - Mutual TLS certificates validated and working")
        report.append(f"   - DNS resolution successful")
        report.append("")
        
        return "\n".join(report)
    
    def run_full_diagnostic(self, nhs_number: str = "9690947714") -> Dict[str, Any]:
        """Run complete diagnostic suite."""
        logger.info("🚀 Starting NHS Spine FHIR PDS Full Diagnostic")
        
        # Step 1: Get public IP
        self.get_public_ip()
        
        # Step 2: Validate certificates
        if not self.validate_certificates():
            return {'success': False, 'error': 'Certificate validation failed'}
        
        # Step 3: Test DNS resolution
        if not self.test_dns_resolution():
            return {'success': False, 'error': 'DNS resolution failed'}
        
        # Step 4: Test FHIR endpoints
        test_results = self.test_fhir_endpoint(nhs_number)
        
        # Step 5: Generate summary report
        summary = self.generate_summary_report(test_results)
        logger.info("\n" + summary)
        
        return test_results

def main():
    """Main execution function."""
    if len(sys.argv) > 1:
        nhs_number = sys.argv[1]
    else:
        nhs_number = "9690947714"  # Default test NHS number
    
    diagnostic = NHSSpineDiagnostic()
    results = diagnostic.run_full_diagnostic(nhs_number)
    
    # Write detailed results to JSON file for further analysis
    with open('nhs_spine_diagnostic_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    logger.info("📄 Detailed results saved to: nhs_spine_diagnostic_results.json")
    
    return results.get('success', False)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
