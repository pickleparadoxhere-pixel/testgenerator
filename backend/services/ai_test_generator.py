import os
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
import ssl

from typing import List
from backend.models.schema import (
    IFlowMetadata, TestCase, Assertion, MockResponseRule, TestSuiteGenerationRequest
)

logger = logging.getLogger(__name__)

class AITestGenerator:
    """Generates comprehensive test suites for SAP iFlows using Google Gemini AI."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")

    def generate_test_suite(self, request: TestSuiteGenerationRequest) -> List[TestCase]:
        metadata = request.iflow_metadata
        
        # Try generating via Gemini AI REST API if key is available
        if self.api_key:
            try:
                ai_test_cases = self._generate_with_gemini_rest(request)
                if ai_test_cases and len(ai_test_cases) > 0:
                    logger.info(f"Successfully generated {len(ai_test_cases)} AI test cases using Gemini AI.")
                    return ai_test_cases
            except Exception as e:
                logger.error(f"Gemini API error, falling back to rule-based engine: {e}", exc_info=True)

        # Fallback to intelligent rule-based synthesis
        logger.info("Using intelligent rule-based engine for test synthesis.")
        return self._generate_rule_based_test_cases(metadata, request.num_cases_per_category)

    def _generate_with_gemini_rest(self, request: TestSuiteGenerationRequest) -> List[TestCase]:
        metadata = request.iflow_metadata
        receivers_summary = ", ".join([f"{r.name} ({r.adapter_type} at {r.url_path})" for r in metadata.receiver_endpoints])
        scripts_summary = ", ".join(metadata.groovy_scripts) if metadata.groovy_scripts else "None"
        mappings_summary = ", ".join(metadata.xslt_mappings) if metadata.xslt_mappings else "None"
        
        prompt = f"""
You are an expert SAP Integration Suite (CPI) QA Automation Engineer.
Generate a comprehensive automated test suite for the following iFlow:

iFlow Name: {metadata.name} (ID: {metadata.id})
Inbound Endpoint: {metadata.inbound_endpoint.name} ({metadata.inbound_endpoint.adapter_type} at {metadata.inbound_endpoint.url_path})
Expected Payload Format: {metadata.inbound_endpoint.payload_format}
Receiver Systems to Mock: {receivers_summary}
Detected Groovy Scripts: {scripts_summary}
Detected XSLT / Mappings: {mappings_summary}

Extracted Schemas / WSDLs / Mappings / Context:
{metadata.inbound_endpoint.raw_schema or 'No schema attached. Construct realistic enterprise SAP business payload fields for this iFlow name.'}

INSTRUCTIONS:
Generate exactly:
- {request.num_cases_per_category} "happy_path" test case(s) (valid business data complying strictly with the iFlow requirements and schemas)
- {request.num_cases_per_category} "boundary" test case(s) (special characters like ÖÄÜ, maximum string lengths, edge case quantities)
- {request.num_cases_per_category} "negative" test case(s) (missing mandatory fields or invalid data types to trigger exception handling)

Respond strictly with a JSON array of objects matching this exact structure (no markdown wrapper, no extra text outside JSON array):
[
  {{
    "id": "TC-001",
    "name": "Happy Path - Valid Business Scenario",
    "category": "happy_path",
    "description": "Tests successful processing with valid payload fields",
    "payload": "<Root><Field>Value</Field></Root>",
    "payload_type": "{metadata.inbound_endpoint.payload_format}",
    "expected_status": 200,
    "assertions": [
      {{"target": "status_code", "operator": "equals", "expected_value": 200}}
    ],
    "mock_rules": [
      {{
        "receiver_name": "{metadata.receiver_endpoints[0].name if metadata.receiver_endpoints else 'Receiver_System'}",
        "match_condition": null,
        "response_status": 200,
        "response_headers": {{"Content-Type": "application/json"}},
        "response_body": "{{\\"status\\": \\"SUCCESS\\", \\"message\\": \\"Mock response\\"}}"
      }}
    ]
  }}
]
"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        req_body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
        }

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url, data=json.dumps(req_body).encode("utf-8"), headers=headers, method="POST")

        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            raw_text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
            
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

            parsed = json.loads(raw_text)
            test_cases = [TestCase(**tc) for tc in parsed]
            return test_cases

    def _generate_rule_based_test_cases(self, metadata: IFlowMetadata, cases_per_cat: int) -> List[TestCase]:
        format_type = metadata.inbound_endpoint.payload_format.upper()
        main_receiver = metadata.receiver_endpoints[0].name if metadata.receiver_endpoints else "Backend_System"

        cases: List[TestCase] = []

        if format_type == "XML":
            happy_payload = f"""<{metadata.id or 'OrderRequest'}>
    <Header>
        <SalesOrg>1010</SalesOrg>
        <DistributionChannel>10</DistributionChannel>
        <CustomerNumber>000100456</CustomerNumber>
        <OrderType>OR</OrderType>
    </Header>
    <Items>
        <Item>
            <ItemNumber>10</ItemNumber>
            <MaterialNumber>MAT-A100</MaterialNumber>
            <Quantity>10</Quantity>
            <Price>150.00</Price>
        </Item>
    </Items>
</{metadata.id or 'OrderRequest'}>"""

            boundary_payload = f"""<{metadata.id or 'OrderRequest'}>
    <Header>
        <SalesOrg>1010</SalesOrg>
        <CustomerNumber>CUST-ÖÄÜ-&amp;-SPECIAL-#12345</CustomerNumber>
        <OrderType>OR</OrderType>
        <Notes>Long text line with 500 characters repeating AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</Notes>
    </Header>
    <Items>
        <Item>
            <ItemNumber>999</ItemNumber>
            <MaterialNumber>MAT-MAX</MaterialNumber>
            <Quantity>999999</Quantity>
        </Item>
    </Items>
</{metadata.id or 'OrderRequest'}>"""

            negative_payload = f"""<{metadata.id or 'OrderRequest'}>
    <Header>
        <!-- Mandatory CustomerNumber omitted -->
        <OrderType>INVALID_TYPE</OrderType>
    </Header>
</{metadata.id or 'OrderRequest'}>"""

        else:
            happy_payload = json.dumps({
                "Header": {
                    "SalesOrg": "1010",
                    "DistributionChannel": "10",
                    "CustomerNumber": "000100456",
                    "OrderType": "OR"
                },
                "Items": [
                    {
                        "ItemNumber": "10",
                        "MaterialNumber": "MAT-A100",
                        "Quantity": 10,
                        "Price": 150.00
                    }
                ]
            }, indent=2)

            boundary_payload = json.dumps({
                "Header": {
                    "SalesOrg": "1010",
                    "CustomerNumber": "CUST-ÖÄÜ-&-SPECIAL-#12345",
                    "OrderType": "OR",
                    "Notes": "A" * 300
                },
                "Items": [
                    {
                        "ItemNumber": "999",
                        "MaterialNumber": "MAT-MAX",
                        "Quantity": 999999,
                        "Price": 0.00
                    }
                ]
            }, indent=2)

            negative_payload = json.dumps({
                "Header": {
                    "OrderType": "INVALID"
                }
            }, indent=2)

        cases.append(TestCase(
            id="TC-001",
            name="Happy Path - Valid Business Transaction",
            category="happy_path",
            description=f"Validates standard processing flow for {metadata.name} when all required fields and business values are correct.",
            payload=happy_payload,
            payload_type=format_type,
            expected_status=200,
            assertions=[
                Assertion(target="status_code", operator="equals", expected_value=200),
                Assertion(target="response_contains", operator="contains", expected_value="SUCCESS")
            ],
            mock_rules=[
                MockResponseRule(
                    receiver_name=main_receiver,
                    response_status=200,
                    response_body=json.dumps({"status": "SUCCESS", "referenceId": "REF-100456", "message": "Processed successfully"})
                )
            ]
        ))

        cases.append(TestCase(
            id="TC-002",
            name="Boundary - Special Characters & Max Limits",
            category="boundary",
            description="Verifies schema resilience against unicode characters, high volume item counts, and boundary values.",
            payload=boundary_payload,
            payload_type=format_type,
            expected_status=200,
            assertions=[
                Assertion(target="status_code", operator="equals", expected_value=200)
            ],
            mock_rules=[
                MockResponseRule(
                    receiver_name=main_receiver,
                    response_status=200,
                    response_body=json.dumps({"status": "SUCCESS", "referenceId": "REF-MAX", "warnings": ["High quantity item"]})
                )
            ]
        ))

        cases.append(TestCase(
            id="TC-003",
            name="Negative - Missing Mandatory Fields",
            category="negative",
            description="Ensures iFlow exception subprocess handles missing fields and returns HTTP 400 Bad Request error.",
            payload=negative_payload,
            payload_type=format_type,
            expected_status=400,
            assertions=[
                Assertion(target="status_code", operator="equals", expected_value=400)
            ],
            mock_rules=[
                MockResponseRule(
                    receiver_name=main_receiver,
                    response_status=400,
                    response_body=json.dumps({"error": "BAD_REQUEST", "message": "CustomerNumber is required"})
                )
            ]
        ))

        return cases
