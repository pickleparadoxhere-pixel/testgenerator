import os
import json
import logging

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
        
        # Try generating via Gemini AI if key is available
        if self.api_key:
            try:
                ai_test_cases = self._generate_with_gemini(request)
                if ai_test_cases:
                    return ai_test_cases
            except Exception as e:
                logger.error(f"Gemini API error, falling back to rule-based engine: {e}")

        # Fallback to intelligent rule-based synthesis
        return self._generate_rule_based_test_cases(metadata, request.num_cases_per_category)

    def _generate_with_gemini(self, request: TestSuiteGenerationRequest) -> List[TestCase]:
        from google import genai
        
        client = genai.Client(api_key=self.api_key)
        metadata = request.iflow_metadata
        
        receivers_summary = ", ".join([f"{r.name} ({r.adapter_type} at {r.url_path})" for r in metadata.receiver_endpoints])
        
        prompt = f"""
You are an expert SAP Integration Suite (CPI) QA Automation Engineer.
Generate a comprehensive automated test suite for the following iFlow:

iFlow Name: {metadata.name}
Inbound Endpoint: {metadata.inbound_endpoint.name} ({metadata.inbound_endpoint.adapter_type} at {metadata.inbound_endpoint.url_path})
Expected Payload Format: {metadata.inbound_endpoint.payload_format}
Receiver Systems to Mock: {receivers_summary}
Raw Schemas / Context:
{metadata.inbound_endpoint.raw_schema or 'No schema attached, use standard B2B/SAP enterprise payload patterns.'}

Generate exactly:
- {request.num_cases_per_category} "happy_path" test cases (valid business data)
- {request.num_cases_per_category} "boundary" test cases (special characters, max lengths, boundary values)
- {request.num_cases_per_category} "negative" test cases (missing required fields, invalid types)

Respond strictly with a JSON array of objects matching this exact structure:
[
  {{
    "id": "TC-001",
    "name": "Happy Path - Valid Sales Order",
    "category": "happy_path",
    "description": "Tests successful order creation with valid items and customer data",
    "payload": "{{\\"OrderHeader\\": {{\\"CustomerNumber\\": \\"100456\\", \\"OrderType\\": \\"OR\\", \\"SalesOrg\\": \\"1010\\"}}, \\"OrderItems\\": [{{\\"ItemNumber\\": \\"10\\", \\"Material\\": \\"MAT-100\\", \\"Quantity\\": 5}}]}}",
    "payload_type": "JSON",
    "expected_status": 200,
    "assertions": [
      {{"target": "status_code", "operator": "equals", "expected_value": 200}},
      {{"target": "$.Status", "operator": "equals", "expected_value": "SUCCESS"}}
    ],
    "mock_rules": [
      {{
        "receiver_name": "S4HANA_Backend",
        "match_condition": null,
        "response_status": 201,
        "response_headers": {{"Content-Type": "application/json"}},
        "response_body": "{{\\"SalesOrder\\": \\"100456\\", \\"Status\\": \\"Created\\"}}"
      }}
    ]
  }}
]
"""
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

        parsed = json.loads(raw_text)
        test_cases = [TestCase(**tc) for tc in parsed]
        return test_cases

    def _generate_rule_based_test_cases(self, metadata: IFlowMetadata, cases_per_cat: int) -> List[TestCase]:
        format_type = metadata.inbound_endpoint.payload_format.upper()
        main_receiver = metadata.receiver_endpoints[0].name if metadata.receiver_endpoints else "Backend_System"

        cases: List[TestCase] = []

        if format_type == "XML":
            # XML test cases
            happy_payload = """<OrderRequest>
    <OrderHeader>
        <SalesOrg>1010</SalesOrg>
        <DistributionChannel>10</DistributionChannel>
        <CustomerNumber>000100456</CustomerNumber>
        <OrderType>OR</OrderType>
    </OrderHeader>
    <OrderItems>
        <Item>
            <ItemNumber>10</ItemNumber>
            <MaterialNumber>MAT-A100</MaterialNumber>
            <Quantity>10</Quantity>
            <Price>150.00</Price>
        </Item>
    </OrderItems>
</OrderRequest>"""

            boundary_payload = """<OrderRequest>
    <OrderHeader>
        <SalesOrg>1010</SalesOrg>
        <CustomerNumber>CUST-ÖÄÜ-&amp;-SPECIAL-#12345</CustomerNumber>
        <OrderType>OR</OrderType>
        <Notes>Long text line with 500 characters repeating AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</Notes>
    </OrderHeader>
    <OrderItems>
        <Item>
            <ItemNumber>999</ItemNumber>
            <MaterialNumber>MAT-MAX</MaterialNumber>
            <Quantity>999999</Quantity>
        </Item>
    </OrderItems>
</OrderRequest>"""

            negative_payload = """<OrderRequest>
    <OrderHeader>
        <!-- CustomerNumber missing -->
        <OrderType>INVALID_TYPE</OrderType>
    </OrderHeader>
</OrderRequest>"""

        else:
            # JSON test cases
            happy_payload = json.dumps({
                "OrderHeader": {
                    "SalesOrg": "1010",
                    "DistributionChannel": "10",
                    "CustomerNumber": "000100456",
                    "OrderType": "OR",
                    "PurchaseOrder": "PO-998877"
                },
                "OrderItems": [
                    {
                        "ItemNumber": "10",
                        "MaterialNumber": "MAT-A100",
                        "Quantity": 10,
                        "Price": 150.00
                    }
                ]
            }, indent=2)

            boundary_payload = json.dumps({
                "OrderHeader": {
                    "SalesOrg": "1010",
                    "CustomerNumber": "CUST-ÖÄÜ-&-SPECIAL-#12345",
                    "OrderType": "OR",
                    "Notes": "A" * 300
                },
                "OrderItems": [
                    {
                        "ItemNumber": "999",
                        "MaterialNumber": "MAT-MAX",
                        "Quantity": 999999,
                        "Price": 0.00
                    }
                ]
            }, indent=2)

            negative_payload = json.dumps({
                "OrderHeader": {
                    "OrderType": "INVALID"
                }
            }, indent=2)

        # 1. Happy Path Case
        cases.append(TestCase(
            id="TC-001",
            name="Happy Path - Valid Business Transaction",
            category="happy_path",
            description="Validates standard processing flow when all required fields and business values are correct.",
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

        # 2. Boundary Case
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

        # 3. Negative Case
        cases.append(TestCase(
            id="TC-003",
            name="Negative - Missing Mandatory Customer Number",
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
