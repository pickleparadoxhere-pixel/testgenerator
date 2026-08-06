import pytest
from backend.services.iflow_parser import IFlowParser
from backend.samples.sample_iflow import create_sample_iflow_zip

def test_iflow_parser_sample_zip():
    parser = IFlowParser()
    zip_bytes = create_sample_iflow_zip()
    metadata = parser.parse_zip(zip_bytes, "SalesOrder_S4HANA_Creation.zip")

    assert metadata.id == "SalesOrder_S4HANA_Creation"
    assert metadata.inbound_endpoint.adapter_type == "HTTPS"
    assert len(metadata.receiver_endpoints) >= 1
    assert "ValidateOrder.groovy" in metadata.groovy_scripts
