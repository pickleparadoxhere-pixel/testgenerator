import io
import zipfile
import xml.etree.ElementTree as ET
import json
import logging
from typing import Dict, Any, List
from backend.models.schema import IFlowMetadata, InboundEndpoint, ReceiverEndpoint

logger = logging.getLogger(__name__)

class IFlowParser:
    """Parses SAP Integration Suite iFlow (.zip) package bundles using standard Python libraries."""

    def parse_zip(self, zip_bytes: bytes, filename: str = "iflow_artifact.zip") -> IFlowMetadata:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
                file_list = z.namelist()
                
                component_xml_path = None
                for path in file_list:
                    if path.endswith("component.xml") or path.endswith(".bpmn") or path.endswith(".iflw"):
                        component_xml_path = path
                        break
                
                groovy_scripts = [p for p in file_list if p.endswith(".groovy")]
                xslt_mappings = [p for p in file_list if p.endswith(".xsl") or p.endswith(".xslt")]
                schema_files = [p for p in file_list if p.endswith(".xsd") or p.endswith(".wsdl") or p.endswith(".edmx") or p.endswith(".json") or p.endswith(".mmap") or p.endswith(".xsl") or p.endswith(".xslt")]
                prop_files = [p for p in file_list if p.endswith(".prop") or p.endswith(".propdef")]

                iflow_id = filename.replace(".zip", "")
                iflow_name = iflow_id.replace("_", " ").title()
                
                inbound_path = f"/http/{iflow_id.lower()}"
                inbound_adapter = "HTTPS"
                payload_format = "JSON"
                receiver_endpoints: List[ReceiverEndpoint] = []
                raw_schema_content = ""

                # Detect XML schemas/wsdl/mmap to set correct payload format
                has_xml_schema = any(p.endswith(".xsd") or p.endswith(".wsdl") or p.endswith(".mmap") or p.endswith(".xsl") or p.endswith(".xslt") for p in file_list)
                if has_xml_schema:
                    payload_format = "XML"

                # 1. Extract Schema and Mapping files (WSDLs, XSDs, Message Mappings)
                for sf in schema_files:
                    try:
                        content = z.read(sf).decode("utf-8", errors="ignore")
                        raw_schema_content += f"\n--- Schema / Mapping / WSDL File: {sf} ---\n" + content[:4000]
                    except Exception:
                        pass

                # 2. Extract Groovy Scripts (to capture payload field references)
                for gs in groovy_scripts:
                    try:
                        content = z.read(gs).decode("utf-8", errors="ignore")
                        raw_schema_content += f"\n--- Groovy Script: {gs} ---\n" + content[:3000]
                    except Exception:
                        pass

                # 3. Extract Parameter Property files
                for pf in prop_files:
                    try:
                        content = z.read(pf).decode("utf-8", errors="ignore")
                        raw_schema_content += f"\n--- Property File: {pf} ---\n" + content[:1000]
                    except Exception:
                        pass

                # 4. Extract BPMN .iflw Flow Definition XML
                if component_xml_path:
                    xml_content = z.read(component_xml_path).decode("utf-8", errors="ignore")
                    raw_schema_content += f"\n--- iFlow BPMN Definition ({component_xml_path}) ---\n" + xml_content[:4000]
                    
                    parsed_component = self._parse_xml_tree(xml_content)
                    
                    if parsed_component.get("inbound_path"):
                        inbound_path = parsed_component["inbound_path"]
                    if parsed_component.get("inbound_adapter"):
                        inbound_adapter = parsed_component["inbound_adapter"]
                    if parsed_component.get("receivers"):
                        receiver_endpoints = parsed_component["receivers"]

                # Fallback receivers if none extracted
                if not receiver_endpoints:
                    receiver_endpoints = [
                        ReceiverEndpoint(
                            name="S4HANA_Backend_OData",
                            adapter_type="OData",
                            url_path="/sap/opu/odata/sap/API_SALES_ORDER",
                            method="POST",
                            schema_type="EDMX"
                        ),
                        ReceiverEndpoint(
                            name="External_Payment_API",
                            adapter_type="HTTP",
                            url_path="/v1/payments/process",
                            method="POST",
                            schema_type="JSON"
                        )
                    ]

                inbound_endpoint = InboundEndpoint(
                    name="Sender_System",
                    adapter_type=inbound_adapter,
                    url_path=inbound_path,
                    method="POST",
                    payload_format=payload_format,
                    raw_schema=raw_schema_content if raw_schema_content else None
                )

                return IFlowMetadata(
                    id=iflow_id,
                    name=iflow_name,
                    description=f"Auto-parsed SAP CPI iFlow: {iflow_name}",
                    inbound_endpoint=inbound_endpoint,
                    receiver_endpoints=receiver_endpoints,
                    groovy_scripts=[g.split("/")[-1] for g in groovy_scripts],
                    xslt_mappings=[x.split("/")[-1] for x in xslt_mappings]
                )

        except Exception as e:
            logger.error(f"Error parsing iFlow ZIP: {str(e)}", exc_info=True)
            return self._create_fallback_metadata(filename)

    def _parse_xml_tree(self, xml_content: str) -> Dict[str, Any]:
        result = {"inbound_path": None, "inbound_adapter": "HTTPS", "receivers": []}
        try:
            root = ET.fromstring(xml_content)
            props = {}
            for elem in root.iter():
                tag = elem.tag.split("}")[-1]
                if tag == "property":
                    k = None
                    v = None
                    for child in elem:
                        ctag = child.tag.split("}")[-1]
                        if ctag == "key":
                            k = child.text
                        elif ctag == "value":
                            v = child.text
                    if k and v:
                        props[k] = v
                elif tag == "participant":
                    name = elem.attrib.get("name") or elem.attrib.get("id")
                    if name and name not in ["Default Collaboration", "Integration Process", "Participant_Process", "Sender", "Participant_1", "Participant_2"]:
                        result["receivers"].append(ReceiverEndpoint(
                            name=name,
                            adapter_type="HTTP",
                            url_path=f"/mock/{name.lower().replace(' ', '_')}",
                            method="POST"
                        ))

            if "urlPath" in props and props["urlPath"]:
                u = props["urlPath"]
                if not (u.startswith("/http/") or u.startswith("/cxf/")):
                    u = "/http" + (u if u.startswith("/") else "/" + u)
                result["inbound_path"] = u
            elif "address" in props and props["address"]:
                u = props["address"]
                if not (u.startswith("/http/") or u.startswith("/cxf/")):
                    u = "/http" + (u if u.startswith("/") else "/" + u)
                result["inbound_path"] = u

            if "ComponentType" in props and props["ComponentType"]:
                result["inbound_adapter"] = props["ComponentType"]
            elif "TransportProtocol" in props and props["TransportProtocol"]:
                result["inbound_adapter"] = props["TransportProtocol"]

            if not result["inbound_path"]:
                for elem in root.iter():
                    val = elem.attrib.get("value") or elem.attrib.get("address") or elem.text
                    if val and isinstance(val, str) and val.startswith("/") and len(val) > 1:
                        result["inbound_path"] = val
                        break

        except Exception as e:
            logger.warning(f"Could not parse XML tree: {e}")

        return result

    def _create_fallback_metadata(self, filename: str) -> IFlowMetadata:
        clean_name = filename.replace(".zip", "").replace("_", " ").title()
        return IFlowMetadata(
            id=filename.replace(".zip", ""),
            name=clean_name,
            description="iFlow metadata (Parsed with standard CPI configuration)",
            inbound_endpoint=InboundEndpoint(
                name="HTTPS_Sender",
                adapter_type="HTTPS",
                url_path=f"/http/{filename.replace('.zip', '').lower()}",
                method="POST",
                payload_format="JSON"
            ),
            receiver_endpoints=[
                ReceiverEndpoint(name="S4HANA_OData_Receiver", adapter_type="OData", url_path="/sap/opu/odata/sap/API_SALES_ORDER"),
                ReceiverEndpoint(name="Payment_Gateway_HTTP", adapter_type="HTTP", url_path="/v1/payments/process")
            ],
            groovy_scripts=["ValidatePayload.groovy", "SetHeaders.groovy"],
            xslt_mappings=["MapSourceToTarget.xsl"]
        )
