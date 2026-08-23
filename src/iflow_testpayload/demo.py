from __future__ import annotations

import zipfile
from pathlib import Path


REQUEST_FILES = {
    "src/main/resources/scenarioflows/integrationflow/Synthetic_Order_Req.iflw": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:ifl="urn:sap:iflow">
  <bpmn2:process id="Synthetic_Request" name="Synthetic Order Request">
    <bpmn2:startEvent id="start" name="HTTPS Order Sender"><bpmn2:extensionElements><ifl:property key="senderAdapter" value="HTTP"/></bpmn2:extensionElements></bpmn2:startEvent>
    <bpmn2:callActivity id="context" name="Set Request Context"><bpmn2:extensionElements>
      <ifl:property key="componentType" value="Content Modifier"/>
      <ifl:property key="customer" value="${header.X-Customer-ID}"/>
      <ifl:property key="environment" value="${property.Environment}"/>
    </bpmn2:extensionElements></bpmn2:callActivity>
    <bpmn2:callActivity id="mapping" name="Map Order to Backend"><bpmn2:extensionElements><ifl:property key="componentType" value="Message Mapping"/></bpmn2:extensionElements></bpmn2:callActivity>
    <bpmn2:exclusiveGateway id="router" name="Environment Router"/>
    <bpmn2:callActivity id="receiver" name="SOAP Backend Receiver"><bpmn2:extensionElements>
      <ifl:property key="receiverAdapter" value="SOAP"/>
      <ifl:property key="credentialAlias" value="${property.BackendCredentialAlias}"/>
    </bpmn2:extensionElements></bpmn2:callActivity>
    <bpmn2:endEvent id="end" name="Request Complete"/>
    <bpmn2:sequenceFlow sourceRef="start" targetRef="context"/>
    <bpmn2:sequenceFlow sourceRef="context" targetRef="mapping"/>
    <bpmn2:sequenceFlow sourceRef="mapping" targetRef="router"/>
    <bpmn2:sequenceFlow name="Test route" sourceRef="router" targetRef="receiver"><bpmn2:conditionExpression>${property.Environment} = 'TEST'</bpmn2:conditionExpression></bpmn2:sequenceFlow>
    <bpmn2:sequenceFlow sourceRef="receiver" targetRef="end"/>
  </bpmn2:process>
  <bpmn2:messageFlow id="request_sender_adapter" name="HTTPS Order Sender Adapter">
    <bpmn2:extensionElements>
      <ifl:property key="direction" value="Sender"/>
      <ifl:property key="ComponentType" value="HTTPS"/>
      <ifl:property key="address" value="/test"/>
    </bpmn2:extensionElements>
  </bpmn2:messageFlow>
</bpmn2:definitions>""",
    "src/main/resources/xsd/order-request.xsd": """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:demo:order:req" elementFormDefault="qualified">
  <xs:element name="CreateOrderRequest" type="CreateOrderRequestType"/>
  <xs:complexType name="CreateOrderRequestType"><xs:sequence>
    <xs:element name="RequestId" type="xs:string"/>
    <xs:element name="CustomerId" type="xs:string"/>
    <xs:element name="OrderDate" type="xs:date"/>
    <xs:element name="Item" minOccurs="1" maxOccurs="unbounded"><xs:complexType><xs:sequence>
      <xs:element name="MaterialId" type="xs:string"/>
      <xs:element name="Quantity" type="xs:decimal"/>
      <xs:element name="Note" type="xs:string" minOccurs="0"/>
    </xs:sequence></xs:complexType></xs:element>
  </xs:sequence></xs:complexType>
</xs:schema>""",
    "src/main/resources/mapping/OrderRequest.mmap": """<?xml version="1.0"?><mapping><map source="/CreateOrderRequest/RequestId" target="/BackendOrder/ExternalId"/><map source="/CreateOrderRequest/CustomerId" target="/BackendOrder/Customer"/></mapping>""",
    "src/main/resources/script/request-context.groovy": """def Message processData(Message message) {
  def correlation = message.getHeader("X-Correlation-ID")
  message.setProperty("RequestReceivedAt", new Date().format("yyyy-MM-dd'T'HH:mm:ss'Z'"))
  return message
}""",
    "src/main/resources/parameters.prop": "Environment=TEST\nBackendUrl=https://example.invalid/synthetic-backend\n",
}


RESPONSE_FILES = {
    "src/main/resources/scenarioflows/integrationflow/Synthetic_Order_Res.iflw": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:ifl="urn:sap:iflow">
  <bpmn2:process id="Synthetic_Response" name="Synthetic Order Response">
    <bpmn2:startEvent id="start" name="JMS Backend Response"><bpmn2:extensionElements><ifl:property key="senderAdapter" value="JMS"/></bpmn2:extensionElements></bpmn2:startEvent>
    <bpmn2:callActivity id="context" name="Set Response Context"><bpmn2:extensionElements>
      <ifl:property key="componentType" value="Content Modifier"/>
      <ifl:property key="processingStatus" value="${property.ProcessingStatus}"/>
      <ifl:property key="correlation" value="${header.X-Correlation-ID}"/>
    </bpmn2:extensionElements></bpmn2:callActivity>
    <bpmn2:exclusiveGateway id="router" name="Status Router"/>
    <bpmn2:callActivity id="receiver" name="HTTPS Client Receiver"><bpmn2:extensionElements><ifl:property key="receiverAdapter" value="HTTP"/></bpmn2:extensionElements></bpmn2:callActivity>
    <bpmn2:endEvent id="end" name="Response Complete"/>
    <bpmn2:sequenceFlow sourceRef="start" targetRef="context"/>
    <bpmn2:sequenceFlow sourceRef="context" targetRef="router"/>
    <bpmn2:sequenceFlow name="Accepted" sourceRef="router" targetRef="receiver"><bpmn2:conditionExpression>${property.ProcessingStatus} = 'ACCEPTED'</bpmn2:conditionExpression></bpmn2:sequenceFlow>
    <bpmn2:sequenceFlow sourceRef="receiver" targetRef="end"/>
  </bpmn2:process>
</bpmn2:definitions>""",
    "src/main/resources/xsd/order-response.xsd": """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:demo:order:res" elementFormDefault="qualified">
  <xs:element name="CreateOrderResponse" type="CreateOrderResponseType"/>
  <xs:complexType name="CreateOrderResponseType"><xs:sequence>
    <xs:element name="RequestId" type="xs:string"/>
    <xs:element name="BackendOrderId" type="xs:string"/>
    <xs:element name="Status"><xs:simpleType><xs:restriction base="xs:string"><xs:enumeration value="ACCEPTED"/><xs:enumeration value="REJECTED"/></xs:restriction></xs:simpleType></xs:element>
    <xs:element name="Message" type="xs:string" minOccurs="0"/>
  </xs:sequence></xs:complexType>
</xs:schema>""",
    "src/main/resources/script/response-context.groovy": """def Message processData(Message message) {
  def backendStatus = message.getProperty("ProcessingStatus")
  def correlation = message.getHeader("X-Correlation-ID")
  message.setHeader("X-Synthetic-Response", "true")
  return message
}""",
    "src/main/resources/parameters.prop": "ResponseChannel=synthetic.order.responses\n",
}


def create_demo_archives(destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    archives: list[Path] = []
    for filename, files in (
        ("Synthetic_Order_Req.zip", REQUEST_FILES),
        ("Synthetic_Order_Res.zip", RESPONSE_FILES),
    ):
        path = destination / filename
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member, content in files.items():
                archive.writestr(member, content)
        archives.append(path)
    return archives
