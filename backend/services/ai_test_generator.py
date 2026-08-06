import os
import json
import logging
import re
import time
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

        # Fallback to intelligent rule-based synthesis tailored to iFlow metadata & schemas
        logger.info("Using intelligent rule-based engine for test synthesis.")
        return self._generate_rule_based_test_cases(metadata, request.num_cases_per_category)

    def _generate_with_gemini_rest(self, request: TestSuiteGenerationRequest) -> List[TestCase]:
        metadata = request.iflow_metadata
        receivers_summary = ", ".join([f"{r.name} ({r.adapter_type} at {r.url_path})" for r in metadata.receiver_endpoints])
        scripts_summary = ", ".join(metadata.groovy_scripts) if metadata.groovy_scripts else "None"
        mappings_summary = ", ".join(metadata.xslt_mappings) if metadata.xslt_mappings else "None"
        
        prompt = f"""
You are an expert SAP Integration Suite (CPI) QA Automation Engineer.
Generate an automated test suite tailored SPECIFICALLY to the following iFlow:

iFlow Name: {metadata.name} (ID: {metadata.id})
Inbound Endpoint: {metadata.inbound_endpoint.name} ({metadata.inbound_endpoint.adapter_type} at {metadata.inbound_endpoint.url_path})
Expected Payload Format: {metadata.inbound_endpoint.payload_format}
Receiver Systems to Mock: {receivers_summary}
Detected Groovy Scripts: {scripts_summary}
Detected XSLT / Mappings: {mappings_summary}

Attached Message Mappings, WSDLs, XSDs, Groovy Scripts, BPMN XML, and iFlow Context:
{metadata.inbound_endpoint.raw_schema or 'No schema attached.'}

CRITICAL MANDATE:
Analyze the attached Message Mapping WSDLs, XSD definitions, Groovy scripts, and BPMN XML to identify the exact SOURCE inbound XML/JSON message structure, target namespace, root element name, complex types, child elements, and attributes.

For example, if a WSDL defines `<element name="ProductHierarchy" type="ProductHierarchy">` under targetNamespace `http://demo.sap.com/mapping/context`, your test payloads MUST be XML matching that exact structure:
`<p1:ProductHierarchy xmlns:p1="http://demo.sap.com/mapping/context"><MainCategory Name="Category1"><Category Name="Sub1"><Product>ItemA</Product></Category></MainCategory></p1:ProductHierarchy>`

Do NOT output generic OrderRequest or JSON payloads when WSDL/XSD XML schemas are attached!

INSTRUCTIONS:
Generate exactly:
- {request.num_cases_per_category} "happy_path" test case(s) (valid business data complying strictly with the iFlow requirements and schemas)
- {request.num_cases_per_category} "boundary" test case(s) (special characters like ÖÄÜ, maximum string lengths, edge case quantities)
- {request.num_cases_per_category} "negative" test case(s) (missing mandatory fields or invalid data types to trigger exception handling)

Respond strictly with a JSON array of objects matching this exact structure (no markdown wrapper, no extra text outside JSON array):
[
  {{
    "id": "TC-001",
    "name": "Happy Path - Valid Scenario for {metadata.name}",
    "category": "happy_path",
    "description": "Tests successful processing with valid payload fields for {metadata.name}",
    "payload": "<p1:Root xmlns:p1=\\"...\\">...</p1:Root>",
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
        "response_body": "{{\\"status\\": \\"SUCCESS\\", \\"iflow\\": \\"{metadata.id}\\"}}"
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
        raw_schema = metadata.inbound_endpoint.raw_schema or ""

        cases: List[TestCase] = []

        # Check for specific SAP WSDL / XSD schemas (like ProductHierarchy)
        if "ProductHierarchy" in raw_schema:
            target_ns = "http://demo.sap.com/mapping/context"
            happy_payload = f"""<p1:ProductHierarchy xmlns:p1="{target_ns}">
    <MainCategory Name="Electronics">
        <Category Name="Laptops">
            <Product>MacBook Pro</Product>
            <Product>ThinkPad X1</Product>
        </Category>
    </MainCategory>
</p1:ProductHierarchy>"""

            boundary_payload = f"""<p1:ProductHierarchy xmlns:p1="{target_ns}">
    <MainCategory Name="ÖÄÜ_Category_&amp;_Special">
        <Category Name="Cat_Max">
            <Product>Product_Long_Text_Limit_Testing_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</Product>
        </Category>
    </MainCategory>
</p1:ProductHierarchy>"""

            negative_payload = f"""<p1:ProductHierarchy xmlns:p1="{target_ns}">
    <!-- Missing mandatory MainCategory element -->
</p1:ProductHierarchy>"""

        elif format_type == "XML":
            discovered_tags = re.findall(r'<([a-zA-Z0-9_]+)>', raw_schema)
            filtered_tags = [t for t in discovered_tags if t not in ["property", "key", "value", "bpmn2", "ifl", "definitions", "collaboration", "participant", "extensionElements"]]
            root_tag = filtered_tags[0] if filtered_tags else f"{metadata.id}Request"
            sub_tag = filtered_tags[1] if len(filtered_tags) > 1 else "Header"
            field_tag = filtered_tags[2] if len(filtered_tags) > 2 else "TransactionID"

            happy_payload = f"""<{root_tag}>
    <{sub_tag}>
        <{field_tag}>TRX-100456</{field_tag}>
        <SourceSystem>SAP_CPI_{metadata.id}</SourceSystem>
        <Timestamp>{time.strftime('%Y-%m-%dT%H:%M:%SZ')}</Timestamp>
    </{sub_tag}>
</{root_tag}>"""

            boundary_payload = f"""<{root_tag}>
    <{sub_tag}>
        <{field_tag}>TRX-ÖÄÜ-&amp;-SPECIAL-#999</{field_tag}>
        <SourceSystem>SAP_CPI_MAX_LIMIT_TEST</SourceSystem>
        <Notes>Long repeating text string AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</Notes>
    </{sub_tag}>
</{root_tag}>"""

            negative_payload = f"""<{root_tag}>
    <{sub_tag}>
        <!-- Mandatory {field_tag} field omitted -->
    </{sub_tag}>
</{root_tag}>"""

        else:
            discovered_keys = re.findall(r'\"([a-zA-Z0-9_]+)\"\s*:', raw_schema)
            key1 = discovered_keys[0] if discovered_keys else "transactionId"
            key2 = discovered_keys[1] if len(discovered_keys) > 1 else "sourceSystem"

            happy_payload = json.dumps({
                metadata.id: {
                    key1: "TRX-100456",
                    key2: f"SAP_CPI_{metadata.id}",
                    "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ')
                }
            }, indent=2)

            boundary_payload = json.dumps({
                metadata.id: {
                    key1: "TRX-ÖÄÜ-&-SPECIAL-#999",
                    key2: "SAP_CPI_MAX_LIMIT_TEST",
                    "notes": "A" * 300
                }
            }, indent=2)

            negative_payload = json.dumps({
                metadata.id: {
                    key2: "INVALID"
                }
            }, indent=2)

        cases.append(TestCase(
            id="TC-001",
            name=f"Happy Path - Valid Scenario for {metadata.name}",
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
                    receiver_name=metadata.receiver_endpoints[0].name if metadata.receiver_endpoints else "Receiver_System",
                    response_status=200,
                    response_body=json.dumps({"status": "SUCCESS", "iFlow": metadata.id, "message": "Processed successfully"})
                )
            ]
        ))

        cases.append(TestCase(
            id="TC-002",
            name=f"Boundary - Special Characters & Limits for {metadata.name}",
            category="boundary",
            description=f"Verifies {metadata.name} resilience against unicode characters, high volume data, and boundary values.",
            payload=boundary_payload,
            payload_type=format_type,
            expected_status=200,
            assertions=[
                Assertion(target="status_code", operator="equals", expected_value=200)
            ],
            mock_rules=[
                MockResponseRule(
                    receiver_name=metadata.receiver_endpoints[0].name if metadata.receiver_endpoints else "Receiver_System",
                    response_status=200,
                    response_body=json.dumps({"status": "SUCCESS", "warnings": ["High length field"]})
                )
            ]
        ))

        cases.append(TestCase(
            id="TC-003",
            name=f"Negative - Missing Mandatory Fields for {metadata.name}",
            category="negative",
            description=f"Ensures {metadata.name} exception subprocess handles missing fields and returns HTTP 400 Bad Request error.",
            payload=negative_payload,
            payload_type=format_type,
            expected_status=400,
            assertions=[
                Assertion(target="status_code", operator="equals", expected_value=400)
            ],
            mock_rules=[
                MockResponseRule(
                    receiver_name=metadata.receiver_endpoints[0].name if metadata.receiver_endpoints else "Receiver_System",
                    response_status=400,
                    response_body=json.dumps({"error": "BAD_REQUEST", "message": "Required field missing"})
                )
            ]
        ))

        return cases
