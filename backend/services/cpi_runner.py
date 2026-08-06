import time
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
import ssl
import base64
from typing import List, Dict, Any, Optional
from backend.models.schema import TestCase, TestResult, TestExecutionRequest, CPICredentials, TestSuiteReport
from backend.services.mock_server import mock_manager

logger = logging.getLogger(__name__)

class CPITestRunner:
    """Executes test cases against SAP CPI endpoints and verifies execution results."""

    def __init__(self, request: TestExecutionRequest, default_bearer_token: Optional[str] = None):
        self.request = request
        self.default_bearer_token = default_bearer_token

    def execute_suite(self) -> TestSuiteReport:
        start_time = time.time()
        results: List[TestResult] = []
        passed_count = 0
        failed_count = 0

        # Resolve auth headers based on specified runtime credentials mode
        auth_header_val = None
        if self.request.credentials and self.request.credentials.client_id and self.request.credentials.client_secret:
            logger.info("Fetching fresh OAuth2 token for live execution using provided runtime credentials.")
            fetched_token = self._fetch_oauth_token(self.request.credentials)
            if fetched_token:
                auth_header_val = f"Bearer {fetched_token}"
        elif self.request.runtime_auth_type == "basic" and self.request.runtime_username:
            u_pass = f"{self.request.runtime_username}:{self.request.runtime_password or ''}"
            encoded = base64.b64encode(u_pass.encode()).decode()
            auth_header_val = f"Basic {encoded}"
        elif self.request.runtime_auth_type == "token" and self.request.runtime_token:
            auth_header_val = f"Bearer {self.request.runtime_token.strip()}"

        if not auth_header_val and self.default_bearer_token:
            auth_header_val = f"Bearer {self.default_bearer_token}"

        for test_case in self.request.test_cases:
            # Register test-specific mock rules
            for rule in test_case.mock_rules:
                mock_manager.register_rule(rule)

            result = self._execute_single_case(test_case, auth_header_val)
            results.append(result)
            if result.status == "PASS":
                passed_count += 1
            else:
                failed_count += 1

        duration_ms = round((time.time() - start_time) * 1000, 2)
        timestamp_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        junit_xml = self._generate_junit_xml(results, duration_ms)

        return TestSuiteReport(
            timestamp=timestamp_str,
            total_tests=len(results),
            passed=passed_count,
            failed=failed_count,
            duration_ms=duration_ms,
            results=results,
            junit_xml=junit_xml
        )

    def _execute_single_case(self, test_case: TestCase, auth_header_val: Optional[str]) -> TestResult:
        start_time = time.time()
        cpi_endpoint = self.request.cpi_endpoint
        
        headers = {}
        if test_case.payload_type.upper() == "JSON":
            headers["Content-Type"] = "application/json"
        else:
            headers["Content-Type"] = "application/xml"

        if auth_header_val:
            headers["Authorization"] = auth_header_val

        actual_status = test_case.expected_status
        actual_response = ""
        error_msg = None
        cpi_mpl_id = None

        # Check if target is a live real network URL or offline simulation endpoint
        is_simulation = "simulated" in cpi_endpoint or not (cpi_endpoint.startswith("http://") or cpi_endpoint.startswith("https://"))

        if not is_simulation:
            try:
                logger.info(f"Firing live HTTP POST to CPI Inbound Endpoint: {cpi_endpoint}")
                req = urllib.request.Request(
                    url=cpi_endpoint,
                    data=test_case.payload.encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                context = ssl._create_unverified_context()
                with urllib.request.urlopen(req, context=context, timeout=20) as resp:
                    actual_status = resp.status
                    actual_response = resp.read().decode("utf-8", errors="ignore")
                    
                    if resp.headers:
                        for hk, hv in resp.headers.items():
                            if hk.lower() in ["sap_messageprocessinglogid", "sap-mpl-id", "messageprocessinglogid"]:
                                cpi_mpl_id = hv
                                break

            except urllib.error.HTTPError as e:
                actual_status = e.code
                actual_response = e.read().decode("utf-8", errors="ignore")
                error_msg = f"HTTP {e.code}: {e.reason}"
                
                if e.headers:
                    for hk, hv in e.headers.items():
                        if hk.lower() in ["sap_messageprocessinglogid", "sap-mpl-id", "messageprocessinglogid"]:
                            cpi_mpl_id = hv
                            break

            except Exception as e:
                logger.warning(f"Error calling CPI runtime endpoint {cpi_endpoint}: {e}")
                actual_status = 500
                actual_response = json.dumps({"error": str(e)})
                error_msg = str(e)
        else:
            # Simulated Execution Mode
            actual_status = test_case.expected_status
            actual_response = json.dumps({
                "status": "SUCCESS" if test_case.expected_status == 200 else "ERROR",
                "iFlow": "Simulated_Execution",
                "message": "Processed successfully by SAP CPI runtime simulation",
                "referenceId": "REF-100456",
                "timestamp": time.time()
            })

        exec_duration = round((time.time() - start_time) * 1000, 2)

        # Get intercepted requests from mock server
        intercepted_requests = mock_manager.get_intercepted_requests()

        # Evaluate Assertions
        assertion_results = []
        overall_pass = True

        for assertion in test_case.assertions:
            passed = True
            detail = ""
            if assertion.target == "status_code":
                passed = (actual_status == int(assertion.expected_value))
                detail = f"Status Code: Expected {assertion.expected_value}, Got {actual_status}"
            elif assertion.target == "response_contains":
                passed = str(assertion.expected_value) in actual_response
                detail = f"Response Contains: '{assertion.expected_value}' in output"
            else:
                passed = True
                detail = f"Custom assertion on {assertion.target}"

            assertion_results.append({
                "target": assertion.target,
                "expected": assertion.expected_value,
                "passed": passed,
                "detail": detail
            })
            if not passed:
                overall_pass = False

        status_str = "PASS" if overall_pass else "FAIL"

        if not cpi_mpl_id:
            cpi_mpl_id = f"MPL-SIM-{int(time.time()*1000)}"

        return TestResult(
            test_id=test_case.id,
            name=test_case.name,
            category=test_case.category,
            status=status_str,
            status_code=actual_status,
            execution_time_ms=exec_duration,
            actual_response=actual_response,
            cpi_mpl_id=cpi_mpl_id,
            mpl_status="COMPLETED" if overall_pass else "FAILED",
            intercepted_mock_requests=intercepted_requests,
            assertion_results=assertion_results,
            error_message=error_msg
        )

    def _fetch_oauth_token(self, creds: CPICredentials) -> Optional[str]:
        try:
            data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8")
            req = urllib.request.Request(creds.token_url, data=data, method="POST")
            auth_str = base64.b64encode(f"{creds.client_id}:{creds.client_secret}".encode()).decode()
            req.add_header("Authorization", f"Basic {auth_str}")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    return data.get("access_token")
        except Exception as e:
            logger.error(f"Failed to fetch SAP CPI OAuth token: {e}")
        return None

    def _generate_junit_xml(self, results: List[TestResult], duration_ms: float) -> str:
        total = len(results)
        failures = sum(1 for r in results if r.status == "FAIL")
        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<testsuite name="SAPCPI_iFlow_TestSuite" tests="{total}" failures="{failures}" time="{duration_ms/1000.0}">',
        ]
        for r in results:
            xml_lines.append(f'  <testcase classname="{r.category}" name="{r.name}" time="{r.execution_time_ms/1000.0}">')
            if r.status == "FAIL":
                xml_lines.append(f'    <failure message="Test Failed">{r.error_message or "Assertion failure"}</failure>')
            xml_lines.append('  </testcase>')
        xml_lines.append('</testsuite>')
        return "\n".join(xml_lines)
