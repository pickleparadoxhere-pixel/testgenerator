import logging
import time
from typing import Dict, List, Any, Optional
from backend.models.schema import MockResponseRule

logger = logging.getLogger(__name__)

class MockServerManager:
    """In-memory dynamic mock server manager for SAP CPI receiver systems."""

    def __init__(self):
        # Store rules by receiver name: {receiver_name: List[MockResponseRule]}
        self._rules: Dict[str, List[MockResponseRule]] = {}
        # Intercepted requests: List[{receiver, timestamp, method, path, headers, body}]
        self._intercepted: List[Dict[str, Any]] = []

    def clear(self):
        self._rules.clear()
        self._intercepted.clear()

    def register_rule(self, rule: MockResponseRule):
        if rule.receiver_name not in self._rules:
            self._rules[rule.receiver_name] = []
        self._rules[rule.receiver_name].append(rule)
        logger.info(f"Registered mock rule for receiver '{rule.receiver_name}' (Status: {rule.response_status})")

    def handle_request(self, receiver_name: str, method: str, path: str, headers: Dict[str, str], body: str) -> Tuple[int, Dict[str, str], str]:
        # Record intercepted request
        intercept_entry = {
            "timestamp": time.time(),
            "receiver_name": receiver_name,
            "method": method,
            "path": path,
            "headers": dict(headers),
            "body": body
        }
        self._intercepted.append(intercept_entry)
        logger.info(f"Mock Receiver '{receiver_name}' intercepted request ({method} {path})")

        # Find matching rule
        rules = self._rules.get(receiver_name, [])
        for rule in rules:
            if rule.match_condition:
                if rule.match_condition in body:
                    return rule.response_status, rule.response_headers, rule.response_body
            else:
                # Default fallback rule if no specific condition
                return rule.response_status, rule.response_headers, rule.response_body

        # If no rule matches, return standard 200 OK mock response
        default_headers = {"Content-Type": "application/json"}
        default_body = f'{{"status": "MOCK_SUCCESS", "receiver": "{receiver_name}", "timestamp": {time.time()}}}'
        return 200, default_headers, default_body

    def get_intercepted_requests(self, receiver_name: Optional[str] = None) -> List[Dict[str, Any]]:
        if receiver_name:
            return [r for r in self._intercepted if r["receiver_name"].lower() == receiver_name.lower()]
        return self._intercepted

# Global singleton mock manager
mock_manager = MockServerManager()
