from typing import List, Dict, Optional, Any

class BaseModel:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
    
    def dict(self, *args, **kwargs):
        res = {}
        for k, v in self.__dict__.items():
            if isinstance(v, BaseModel):
                res[k] = v.dict()
            elif isinstance(v, list):
                res[k] = [i.dict() if isinstance(i, BaseModel) else i for i in v]
            else:
                res[k] = v
        return res

    def json(self, *args, **kwargs):
        import json
        return json.dumps(self.dict())

class CPICredentials(BaseModel):
    def __init__(self, tenant_url: str = "", client_id: str = "", client_secret: str = "", token_url: str = "", **kwargs):
        super().__init__(**kwargs)
        self.tenant_url = tenant_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = token_url

class ReceiverEndpoint(BaseModel):
    def __init__(self, name: str = "", adapter_type: str = "HTTP", url_path: str = None, method: str = "POST", schema_type: str = None, raw_schema: str = None, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.adapter_type = adapter_type
        self.url_path = url_path
        self.method = method
        self.schema_type = schema_type
        self.raw_schema = raw_schema

    @classmethod
    def from_dict(cls, d: Any):
        if isinstance(d, cls):
            return d
        if isinstance(d, dict):
            return cls(**d)
        return cls()

class InboundEndpoint(BaseModel):
    def __init__(self, name: str = "", adapter_type: str = "HTTPS", url_path: str = "", method: str = "POST", payload_format: str = "XML", raw_schema: str = None, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.adapter_type = adapter_type
        self.url_path = url_path
        self.method = method
        self.payload_format = payload_format
        self.raw_schema = raw_schema

    @classmethod
    def from_dict(cls, d: Any):
        if isinstance(d, cls):
            return d
        if isinstance(d, dict):
            return cls(**d)
        return cls()

class IFlowMetadata(BaseModel):
    def __init__(self, id: str = "", name: str = "", description: str = None, inbound_endpoint: Any = None, receiver_endpoints: Any = None, groovy_scripts: List[str] = None, xslt_mappings: List[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.id = id
        self.name = name
        self.description = description
        
        self.inbound_endpoint = InboundEndpoint.from_dict(inbound_endpoint)
        
        receivers = receiver_endpoints or []
        self.receiver_endpoints = [ReceiverEndpoint.from_dict(r) for r in receivers]
        
        self.groovy_scripts = groovy_scripts or []
        self.xslt_mappings = xslt_mappings or []

    @classmethod
    def from_dict(cls, d: Any):
        if isinstance(d, cls):
            return d
        if isinstance(d, dict):
            return cls(**d)
        return cls()

class Assertion(BaseModel):
    def __init__(self, target: str = "", operator: str = "equals", expected_value: Any = None, **kwargs):
        super().__init__(**kwargs)
        self.target = target
        self.operator = operator
        self.expected_value = expected_value

    @classmethod
    def from_dict(cls, d: Any):
        if isinstance(d, cls):
            return d
        if isinstance(d, dict):
            return cls(**d)
        return cls()

class MockResponseRule(BaseModel):
    def __init__(self, receiver_name: str = "", match_condition: str = None, response_status: int = 200, response_headers: Dict[str, str] = None, response_body: str = "", **kwargs):
        super().__init__(**kwargs)
        self.receiver_name = receiver_name
        self.match_condition = match_condition
        self.response_status = response_status
        self.response_headers = response_headers or {"Content-Type": "application/json"}
        self.response_body = response_body

    @classmethod
    def from_dict(cls, d: Any):
        if isinstance(d, cls):
            return d
        if isinstance(d, dict):
            return cls(**d)
        return cls()

class TestCase(BaseModel):
    def __init__(self, id: str = "", name: str = "", category: str = "happy_path", description: str = "", payload: str = "", payload_type: str = "JSON", expected_status: int = 200, assertions: Any = None, mock_rules: Any = None, **kwargs):
        super().__init__(**kwargs)
        self.id = id
        self.name = name
        self.category = category
        self.description = description
        self.payload = payload
        self.payload_type = payload_type
        self.expected_status = expected_status
        
        raw_assertions = assertions or []
        self.assertions = [Assertion.from_dict(a) for a in raw_assertions]
        
        raw_mocks = mock_rules or []
        self.mock_rules = [MockResponseRule.from_dict(m) for m in raw_mocks]

    @classmethod
    def from_dict(cls, d: Any):
        if isinstance(d, cls):
            return d
        if isinstance(d, dict):
            return cls(**d)
        return cls()

class TestSuiteGenerationRequest(BaseModel):
    def __init__(self, iflow_metadata: Any = None, num_cases_per_category: int = 2, custom_instructions: str = None, **kwargs):
        super().__init__(**kwargs)
        self.iflow_metadata = IFlowMetadata.from_dict(iflow_metadata)
        self.num_cases_per_category = num_cases_per_category
        self.custom_instructions = custom_instructions

class TestExecutionRequest(BaseModel):
    def __init__(self, cpi_endpoint: str = "", credentials: Any = None, test_cases: Any = None, enable_mpl_check: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.cpi_endpoint = cpi_endpoint
        self.credentials = CPICredentials(**credentials) if isinstance(credentials, dict) else credentials
        raw_cases = test_cases or []
        self.test_cases = [TestCase.from_dict(tc) for tc in raw_cases]
        self.enable_mpl_check = enable_mpl_check

class TestResult(BaseModel):
    def __init__(self, test_id: str = "", name: str = "", category: str = "", status: str = "PASS", status_code: int = 200, execution_time_ms: float = 0.0, actual_response: str = "", cpi_mpl_id: str = None, mpl_status: str = None, intercepted_mock_requests: List[Dict[str, Any]] = None, assertion_results: List[Dict[str, Any]] = None, error_message: str = None, **kwargs):
        super().__init__(**kwargs)
        self.test_id = test_id
        self.name = name
        self.category = category
        self.status = status
        self.status_code = status_code
        self.execution_time_ms = execution_time_ms
        self.actual_response = actual_response
        self.cpi_mpl_id = cpi_mpl_id
        self.mpl_status = mpl_status
        self.intercepted_mock_requests = intercepted_mock_requests or []
        self.assertion_results = assertion_results or []
        self.error_message = error_message

class TestSuiteReport(BaseModel):
    def __init__(self, timestamp: str = "", total_tests: int = 0, passed: int = 0, failed: int = 0, duration_ms: float = 0.0, results: List[TestResult] = None, junit_xml: str = None, **kwargs):
        super().__init__(**kwargs)
        self.timestamp = timestamp
        self.total_tests = total_tests
        self.passed = passed
        self.failed = failed
        self.duration_ms = duration_ms
        self.results = results or []
        self.junit_xml = junit_xml
