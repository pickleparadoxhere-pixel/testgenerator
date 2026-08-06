import pytest
from backend.services.mock_server import MockServerManager
from backend.models.schema import MockResponseRule

def test_mock_server_registration_and_intercept():
    manager = MockServerManager()
    manager.clear()

    rule = MockResponseRule(
        receiver_name="S4HANA_Backend",
        response_status=201,
        response_body='{"SalesOrder": "100456"}'
    )
    manager.register_rule(rule)

    status, headers, body = manager.handle_request(
        receiver_name="S4HANA_Backend",
        method="POST",
        path="/mock/s4hana",
        headers={"Content-Type": "application/json"},
        body='{"Customer": "1001"}'
    )

    assert status == 201
    assert 'SalesOrder' in body

    intercepts = manager.get_intercepted_requests("S4HANA_Backend")
    assert len(intercepts) == 1
    assert intercepts[0]["receiver_name"] == "S4HANA_Backend"
