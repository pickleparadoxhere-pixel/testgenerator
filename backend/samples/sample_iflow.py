import io
import zipfile

def create_sample_iflow_zip() -> bytes:
    """Generates a valid in-memory sample iFlow .zip bundle for demo and testing purposes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # component.xml definition
        component_xml = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL">
    <bpmn2:process id="Process_1" name="SalesOrder_S4HANA_Creation">
        <bpmn2:extensionElements>
            <entry key="inbound_path" value="/cxf/sales/order/create"/>
            <entry key="inbound_adapter" value="HTTPS"/>
            <entry key="receiver" value="S4HANA_Backend_OData"/>
            <entry key="receiver" value="External_Payment_API"/>
        </bpmn2:extensionElements>
    </bpmn2:process>
</bpmn2:definitions>"""
        z.writestr("src/main/resources/scenario_bundles/xml/component.xml", component_xml)

        # XSD schema
        xsd_schema = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xs:element name="OrderRequest">
        <xs:complexType>
            <xs:sequence>
                <xs:element name="SalesOrg" type="xs:string"/>
                <xs:element name="CustomerNumber" type="xs:string"/>
                <xs:element name="OrderType" type="xs:string"/>
            </xs:sequence>
        </xs:complexType>
    </xs:element>
</xs:schema>"""
        z.writestr("src/main/resources/xsd/SalesOrder.xsd", xsd_schema)

        # Groovy script
        groovy_script = """import com.sap.gateway.ip.core.customdev.util.Message;

def Message processData(Message message) {
    def body = message.getBody(String.class);
    message.setHeader("ProcessedBy", "SAP CPI AI Agent");
    return message;
}"""
        z.writestr("src/main/resources/script/ValidateOrder.groovy", groovy_script)

    buf.seek(0)
    return buf.getvalue()
                    
