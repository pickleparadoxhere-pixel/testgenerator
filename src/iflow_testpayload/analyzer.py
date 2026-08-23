from __future__ import annotations

import json
import re
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree as ET


class AnalysisError(RuntimeError):
    pass


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def safe_text(element: ET.Element | None) -> str:
    return "" if element is None else " ".join("".join(element.itertext()).split())


@dataclass
class ConfigEntry:
    step: str
    kind: str
    action: str
    name: str
    value: str


@dataclass
class Requirement:
    name: str
    sample: str
    mandatory: bool
    notes: str


@dataclass
class Payload:
    scenario: str
    format: str
    body: str
    source: str


@dataclass
class Analysis:
    name: str
    inventory: dict[str, list[str]] = field(default_factory=dict)
    sender: str = "Not determined"
    receiver: str = "Not determined"
    steps: list[str] = field(default_factory=list)
    config: list[ConfigEntry] = field(default_factory=list)
    headers: list[Requirement] = field(default_factory=list)
    properties: list[Requirement] = field(default_factory=list)
    payloads: list[Payload] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    @staticmethod
    def _cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")

    def to_markdown(self) -> str:
        lines = [f"# SAP CPI IFlow Test Payload Report: {self.name}", ""]
        lines += ["## Package Inventory", ""]
        for kind, files in self.inventory.items():
            lines.append(f"- **{kind}:** " + (", ".join(f"`{x}`" for x in files) if files else "None"))
        lines += ["", "## 1. IFlow Summary", ""]
        lines += [f"- **Name:** {self.name}", f"- **Sender:** {self.sender}", f"- **Receiver:** {self.receiver}"]
        lines.append("- **Sequence:** " + (" → ".join(self.steps) if self.steps else "Not determined"))
        lines += ["", "## 2. Extracted Configuration Table", "", "| Step | Type | Sets/Reads | Name | Value/Source |", "|---|---|---|---|---|"]
        if self.config:
            for item in self.config:
                lines.append("| " + " | ".join(self._cell(x) for x in (item.step, item.kind, item.action, item.name, item.value)) + " |")
        else:
            lines.append("| — | — | — | — | No configuration values could be extracted |")
        lines += ["", "## 3. Required Headers", "", "| Header Name | Sample Value | Mandatory? | Notes |", "|---|---|---|---|"]
        lines += self._requirements_table(self.headers)
        lines += ["", "## 4. Required Exchange Properties", "", "| Property Name | Sample Value | Mandatory? | Notes |", "|---|---|---|---|"]
        lines += self._requirements_table(self.properties)
        lines += ["", "## 5. Test Payload(s)", ""]
        for payload in self.payloads:
            lines += [f"### Scenario: {payload.scenario}", "", f"Derived from `{payload.source}`.", "", f"```{payload.format}", payload.body, "```", ""]
        if not self.payloads:
            lines += ["No authoritative source schema was found, so a payload was not fabricated.", ""]
        lines += ["## 6. Assumptions & Gaps", ""]
        lines += [f"- {x}" for x in self.assumptions] or ["- None."]
        return "\n".join(lines).rstrip() + "\n"

    def _requirements_table(self, requirements: list[Requirement]) -> list[str]:
        if not requirements:
            return ["| — | — | No | None detected in static artifacts |"]
        return [
            "| " + " | ".join(self._cell(x) for x in (r.name, r.sample, "Yes" if r.mandatory else "No", r.notes)) + " |"
            for r in requirements
        ]


class IFlowAnalyzer:
    GROUPS = {
        "IFlow definitions": {".iflw"},
        "Mappings": {".mmap", ".cml"},
        "Schemas": {".xsd", ".wsdl", ".json"},
        "Scripts": {".groovy", ".js"},
        "Parameters / metadata": {".prop", ".propdef", ".mf"},
        "Other": set(),
    }
    XML_SCHEMA = "http://www.w3.org/2001/XMLSchema"

    def __init__(self, source: Path):
        self.source = source.expanduser().resolve()
        self._temp: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None
        self.files: list[Path] = []
        self.parameters: dict[str, str] = {}
        self.internally_set_properties: set[str] = set()
        self.dynamic_mapping_properties: set[str] = set()
        self.referenced_scripts: set[str] = set()
        self.referenced_schema_files: set[str] = set()
        self.schema_message_roles: dict[tuple[str, str], str] = {}

    def analyze(self) -> Analysis:
        try:
            self.root = self._prepare_source()
            self.files = sorted(
                p
                for p in self.root.rglob("*")
                if p.is_file()
                and "__MACOSX" not in p.parts
                and not p.name.startswith("._")
                and p.name != ".DS_Store"
            )
            if not self.files:
                raise AnalysisError("the input contains no files")
            result = Analysis(name=self.source.stem)
            result.inventory = self._inventory()
            self.parameters = self._read_parameters()
            reference_text = " ".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in self.files
                if path.suffix.lower() in {".iflw", ".mmap"}
            )
            self.referenced_scripts = {
                path.name
                for path in self.files
                if path.suffix.lower() in {".groovy", ".js"} and path.name in reference_text
            }
            iflow_files = [p for p in self.files if p.suffix.lower() == ".iflw"]
            flow_names: list[str] = []
            for path in iflow_files:
                self._parse_iflow(path, result)
                flow_names.append(result.name)
            if len(iflow_files) > 1:
                result.name = f"Combined analysis ({len(iflow_files)} IFlows)"
                result.assumptions.append("IFlows included: " + ", ".join(dict.fromkeys(flow_names)) + ".")
            for path in self.files:
                if path.suffix.lower() == ".mmap":
                    self._parse_mapping(path, result)
            for path in self.files:
                if path.suffix.lower() in {".groovy", ".js"}:
                    self._parse_script(path, result)
            self._generate_payloads(result)
            self._deduplicate(result)
            for requirement in result.properties:
                if (
                    requirement.mandatory
                    and requirement.name not in self.parameters
                    and requirement.name not in self.internally_set_properties
                    and requirement.sample.startswith("TEST-")
                ):
                    result.assumptions.append(
                        f"Exchange property `{requirement.name}` is read at runtime but has no static default in parameters.prop."
                    )
            if not iflow_files:
                result.assumptions.append("No .iflw definition was present; sender, receiver, and flow order could not be determined.")
            if not self.parameters:
                result.assumptions.append("No externalized parameter values were found; unresolved placeholders remain unchanged.")
            return result
        except (OSError, ET.ParseError, zipfile.BadZipFile) as exc:
            raise AnalysisError(str(exc)) from exc
        finally:
            if self._temp:
                self._temp.cleanup()

    def _prepare_source(self) -> Path:
        if not self.source.exists():
            raise AnalysisError(f"input does not exist: {self.source}")
        if self.source.is_dir():
            return self.source
        if self.source.suffix.lower() != ".zip":
            raise AnalysisError("input must be an extracted directory or .zip file")
        self._temp = tempfile.TemporaryDirectory(prefix="iflow-analysis-")
        destination = Path(self._temp.name).resolve()
        with zipfile.ZipFile(self.source) as archive:
            for member in archive.infolist():
                target = (destination / member.filename).resolve()
                if destination not in target.parents and target != destination:
                    raise AnalysisError(f"unsafe ZIP member: {member.filename}")
            archive.extractall(destination)
        return destination

    def _relative(self, path: Path) -> str:
        assert self.root
        return path.relative_to(self.root).as_posix()

    def _inventory(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {key: [] for key in self.GROUPS}
        for path in self.files:
            suffix = path.suffix.lower()
            group = next((name for name, suffixes in self.GROUPS.items() if suffix in suffixes), "Other")
            grouped[group].append(self._relative(path))
        return {key: value for key, value in grouped.items() if value}

    def _read_parameters(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for path in self.files:
            if path.name not in {"parameters.prop", "parameters.propdef"}:
                continue
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith(("#", "!")) or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                result[key.strip()] = value.strip()
        return result

    def _resolve(self, value: str) -> str:
        def replacement(match: re.Match[str]) -> str:
            return self.parameters.get(match.group(1), match.group(0))
        return re.sub(r"\$\{(?:property\.)?([^}]+)\}", replacement, value)

    def _parse_iflow(self, path: Path, result: Analysis) -> None:
        tree = ET.parse(path)
        root = tree.getroot()
        process = next((x for x in root.iter() if local_name(x.tag) in {"process", "integrationProcess"}), None)
        if process is not None and process.attrib.get("name"):
            result.name = process.attrib["name"]
        nodes: dict[str, tuple[str, str]] = {}
        edges: list[tuple[str, str, str]] = []
        executable = {"startEvent", "endEvent", "serviceTask", "callActivity", "scriptTask", "exclusiveGateway", "parallelGateway", "intermediateCatchEvent", "intermediateThrowEvent", "sendTask", "receiveTask"}
        for element in root.iter():
            tag = local_name(element.tag)
            if tag == "sequenceFlow":
                condition = next((safe_text(x) for x in element if local_name(x.tag) == "conditionExpression"), "")
                edges.append((element.attrib.get("sourceRef", ""), element.attrib.get("targetRef", ""), condition))
                if condition:
                    result.config.append(ConfigEntry(element.attrib.get("name", "Route"), "Router", "Reads", "condition", condition))
            if tag == "messageFlow":
                label = element.attrib.get("name") or "Adapter"
                props = self._element_properties(element)
                normalized_props = {key.lower(): value for key, value in props.items()}
                direction = normalized_props.get("direction", "").lower()
                adapter_kind = "Sender Adapter" if direction == "sender" else "Receiver Adapter" if direction == "receiver" else "Adapter"
                for key, value in props.items():
                    if value:
                        result.config.append(ConfigEntry(label, adapter_kind, "Configures", key, self._resolve(value)))
                component = normalized_props.get("componenttype") or normalized_props.get("adaptertype") or normalized_props.get("name") or label
                if adapter_kind == "Sender Adapter":
                    result.sender = component
                elif adapter_kind == "Receiver Adapter":
                    result.receiver = component
            if tag not in executable:
                continue
            identifier = element.attrib.get("id", "")
            label = element.attrib.get("name") or tag
            props = self._element_properties(element)
            kind = self._classify_step(tag, element, props)
            nodes[identifier] = (label, kind)
            for key, value in props.items():
                if not value or key.lower() in {"id", "name"}:
                    continue
                action = "Sets" if any(x in key.lower() for x in ("header", "property", "value")) else "Configures"
                result.config.append(ConfigEntry(label, kind, action, key, self._resolve(value)))
                self._requirements_from_text(value, f"{label} ({key})", result)
                if key.lower() in {"propertytable", "headertable"} and value:
                    self._parse_modifier_table(label, kind, key, value, result)
        result.steps.extend(self._ordered_steps(nodes, edges))
        all_text = " ".join([str(x.attrib) + " " + safe_text(x) for x in root.iter()])
        sender_match = re.search(r"(?:sender|inbound)[^\n]{0,120}?(HTTP|HTTPS|SOAP|SFTP|FTP|IDoc|OData|JMS|AMQP|Mail)", all_text, re.I)
        receiver_match = re.search(r"(?:receiver|outbound)[^\n]{0,120}?(HTTP|HTTPS|SOAP|SFTP|FTP|IDoc|OData|JMS|AMQP|Mail)", all_text, re.I)
        if sender_match:
            result.sender = sender_match.group(1).upper()
        if receiver_match:
            result.receiver = receiver_match.group(1).upper()
        for label, kind in nodes.values():
            if result.sender == "Not determined" and kind == "Sender Adapter":
                result.sender = label
            if result.receiver == "Not determined" and kind == "Receiver Adapter":
                result.receiver = label

    def _element_properties(self, element: ET.Element) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in element.attrib.items():
            normalized = local_name(key)
            if normalized not in {"id", "name"}:
                result[normalized] = value
        descendants = list(element.iter())
        for index, item in enumerate(descendants):
            if local_name(item.tag).lower() not in {"property", "parameter"}:
                continue
            key = item.attrib.get("key") or item.attrib.get("name")
            value = item.attrib.get("value")
            children = {local_name(x.tag).lower(): safe_text(x) for x in item}
            key = key or children.get("key") or children.get("name")
            value = value if value is not None else children.get("value", safe_text(item))
            if key:
                result[key] = value
        return result

    def _classify_step(self, tag: str, element: ET.Element, props: dict[str, str]) -> str:
        haystack = " ".join([tag, element.attrib.get("name", ""), *element.attrib.values(), *props.keys(), *props.values()]).lower()
        if any(key.lower() in {"propertytable", "headertable"} for key in props):
            return "Content Modifier"
        for needle, label in (("content modifier", "Content Modifier"), ("contentmodifier", "Content Modifier"), ("router", "Router"), ("mapping", "Message Mapping"), ("groovy", "Groovy Script"), ("request reply", "Request-Reply"), ("requestreply", "Request-Reply"), ("sender", "Sender Adapter"), ("receiver", "Receiver Adapter")):
            if needle in haystack:
                return label
        return {"startEvent": "Start", "endEvent": "End", "exclusiveGateway": "Router", "scriptTask": "Script"}.get(tag, tag)

    def _parse_modifier_table(self, label: str, kind: str, table_name: str, value: str, result: Analysis) -> None:
        try:
            table = ET.fromstring(f"<table>{value}</table>")
        except ET.ParseError:
            return
        entry_kind = "Header" if table_name.lower() == "headertable" else "Property"
        for row in table.findall("row"):
            cells = {cell.attrib.get("id", ""): safe_text(cell) for cell in row.findall("cell")}
            name = cells.get("Name", "")
            if not name:
                continue
            source_type = cells.get("Type", "constant")
            source_value = cells.get("Value", "") or cells.get("Default", "")
            result.config.append(ConfigEntry(label, kind, "Sets", name, f"{source_type}: {source_value}"))
            if entry_kind == "Property":
                self.internally_set_properties.add(name)

    def _ordered_steps(self, nodes: dict[str, tuple[str, str]], edges: list[tuple[str, str, str]]) -> list[str]:
        incoming = {target for _, target, _ in edges}
        starts = [node for node in nodes if node not in incoming]
        adjacency: dict[str, list[str]] = defaultdict(list)
        for source, target, _ in edges:
            adjacency[source].append(target)
        ordered: list[str] = []
        seen: set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in seen:
                return
            seen.add(identifier)
            if identifier in nodes:
                label, kind = nodes[identifier]
                ordered.append(f"{label} ({kind})")
            for target in adjacency[identifier]:
                visit(target)

        for start in starts or list(nodes):
            visit(start)
        return ordered

    def _parse_script(self, path: Path, result: Analysis) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        label = self._relative(path)
        is_referenced = path.name in self.referenced_scripts
        if "DynamicConfiguration" in text and is_referenced:
            result.assumptions.append(
                f"Script `{label}` reads SAP DynamicConfiguration at runtime; supply adapter metadata such as the source file name."
            )
        if (
            re.search(r"getProperty\s*\(\s*[A-Za-z_]\w*\s*\)", text)
            and is_referenced
            and not self.dynamic_mapping_properties
        ):
            result.assumptions.append(
                f"Script `{label}` reads a property whose name is passed dynamically, so the exact property cannot be enumerated statically."
            )
        if re.search(r"\bnew\s+Date\s*\(|\b(?:now|currentTimeMillis)\s*\(", text):
            result.assumptions.append(f"Script `{label}` creates a runtime timestamp; the generated payload uses a stable sample date instead.")
        patterns = [
            (r"getHeader\s*\(\s*['\"]([^'\"]+)", "Header", "Reads"),
            (r"setHeader\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([^\n;)]+)", "Header", "Sets"),
            (r"getProperty\s*\(\s*['\"]([^'\"]+)", "Property", "Reads"),
            (r"setProperty\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([^\n;)]+)", "Property", "Sets"),
        ]
        for pattern, kind, action in patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1)
                value = match.group(2).strip() if match.lastindex and match.lastindex > 1 else "runtime value"
                result.config.append(ConfigEntry(label, f"{path.suffix[1:].title()} Script", action, name, value))
                target = result.headers if kind == "Header" else result.properties
                sample = self.parameters.get(name, f"TEST-{name.upper()}") if kind == "Property" else f"TEST-{name.upper()}"
                target.append(Requirement(name, sample, action == "Reads", f"{action} by {label}"))

    def _parse_mapping(self, path: Path, result: Analysis) -> None:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            result.assumptions.append(f"Mapping `{self._relative(path)}` was not parseable XML.")
            return
        for link_role in (element for element in root.iter() if local_name(element.tag).lower() == "lnkrole"):
            declared_role = link_role.attrib.get("role", "").upper()
            if "SOURCE" not in declared_role and "TARGET" not in declared_role:
                continue
            values = [safe_text(element) for element in link_role.iter() if local_name(element.tag).lower() == "elem"]
            schema_file = next((value for value in values if value.lower().endswith((".xsd", ".wsdl"))), "")
            if not schema_file:
                continue
            message_name = values[2] if len(values) > 2 else ""
            role = "Source" if "SOURCE" in declared_role else "Target"
            self.referenced_schema_files.add(Path(schema_file).name)
            if message_name:
                self.schema_message_roles[(Path(schema_file).name, message_name)] = role
        for element in root.iter():
            tag = local_name(element.tag).lower()
            if "mapping" not in tag and tag not in {"map", "connection"}:
                continue
            source = element.attrib.get("source") or element.attrib.get("sourcePath") or element.attrib.get("from")
            target = element.attrib.get("target") or element.attrib.get("targetPath") or element.attrib.get("to")
            if source or target:
                result.config.append(ConfigEntry(self._relative(path), "Message Mapping", "Maps", target or "target", source or safe_text(element)))
        for brick in (element for element in root.iter() if local_name(element.tag).lower() == "brick" and element.attrib.get("fname") == "getProperty"):
            property_name = ""
            for parameter in brick.iter():
                if local_name(parameter.tag).lower() != "param" or parameter.attrib.get("name") != "value":
                    continue
                value_node = next((child for child in parameter if local_name(child.tag).lower() == "value"), None)
                property_name = safe_text(value_node)
                if property_name:
                    break
            if not property_name:
                continue
            self.dynamic_mapping_properties.add(property_name)
            if property_name in self.internally_set_properties:
                result.config.append(ConfigEntry(self._relative(path), "Message Mapping", "Reads", property_name, "Set earlier by Content Modifier"))
                continue
            sample = "ACKFILE0001PSR.xml" if property_name == "CamelFileExchangeFile" else f"TEST-{property_name.upper()}"
            result.properties.append(
                Requirement(property_name, sample, True, f"Concrete argument passed to dynamic getProperty() in {self._relative(path)}")
            )
            result.config.append(ConfigEntry(self._relative(path), "Message Mapping", "Reads", property_name, f"runtime property; sample `{sample}`"))

    def _requirements_from_text(self, text: str, source: str, result: Analysis) -> None:
        for kind, name in re.findall(r"\$\{(header|property)\.([^}]+)\}", text, re.I):
            target = result.headers if kind.lower() == "header" else result.properties
            sample = self.parameters.get(name, f"TEST-{name.upper()}") if kind.lower() == "property" else f"TEST-{name.upper()}"
            target.append(Requirement(name, sample, True, f"Referenced by {source}"))
        for kind, name in re.findall(r"\b(header|property)\.([A-Za-z_][\w.-]*)", text, re.I):
            target = result.headers if kind.lower() == "header" else result.properties
            sample = self.parameters.get(name, f"TEST-{name.upper()}") if kind.lower() == "property" else f"TEST-{name.upper()}"
            target.append(Requirement(name, sample, True, f"Referenced by {source}"))

    def _generate_payloads(self, result: Analysis) -> None:
        xsd_files = [p for p in self.files if p.suffix.lower() == ".xsd"]
        all_wsdl_files = [p for p in self.files if p.suffix.lower() == ".wsdl"]
        wsdl_files = [
            path
            for path in all_wsdl_files
            if not self.referenced_schema_files or path.name in self.referenced_schema_files
        ]
        xml_schema_files = xsd_files + wsdl_files
        json_schemas = [p for p in self.files if p.suffix.lower() == ".json"]
        schema_profiles: dict[Path, tuple[str, bool, str]] = {}
        for path in xml_schema_files:
            try:
                schema = self._schema_element(path)
                has_document_root = bool(schema.findall(f"{{{self.XML_SCHEMA}}}element"))
                first_root = schema.find(f"{{{self.XML_SCHEMA}}}element")
                schema_profiles[path] = (
                    schema.attrib.get("targetNamespace", ""),
                    has_document_root,
                    first_root.attrib.get("name", "") if first_root is not None else "",
                )
            except (ET.ParseError, ValueError):
                schema_profiles[path] = ("", False, "")
        rooted_namespaces = {namespace for namespace, has_root, _ in schema_profiles.values() if namespace and has_root}
        generated_documents: set[tuple[str, str]] = set()
        for path in xml_schema_files:
            namespace, has_document_root, root_name = schema_profiles[path]
            if path.suffix.lower() == ".xsd" and not has_document_root and namespace in rooted_namespaces:
                # CPI exports frequently include a DT_* type-only schema alongside
                # an MT_* document schema in the same namespace. The MT_* file is
                # the authoritative payload source, so the DT_* file is supporting
                # metadata rather than a failed payload candidate.
                continue
            signature = (namespace, root_name)
            if root_name and signature in generated_documents:
                continue
            try:
                body = self._sample_xsd(path)
            except (ET.ParseError, ValueError) as exc:
                result.assumptions.append(f"Could not generate XML from `{self._relative(path)}`: {exc}")
                continue
            if not has_document_root:
                result.assumptions.append(
                    f"Schema `{self._relative(path)}` defines only named types and no companion document schema was found; the first named complex type was used as the inferred root."
                )
            if root_name:
                generated_documents.add(signature)
            document_role = self._schema_role(path, root_name)
            result.payloads.append(Payload(f"{document_role} happy path — {root_name or path.stem}", "xml", body, self._relative(path)))
        for path in json_schemas:
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(schema, dict) or not any(key in schema for key in ("$schema", "type", "properties", "$ref")):
                    continue
                sample = self._sample_json(schema, schema)
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                result.assumptions.append(f"Could not generate JSON from `{self._relative(path)}`: {exc}")
                continue
            result.payloads.append(Payload(f"Happy path — {path.stem}", "json", json.dumps(sample, indent=2), self._relative(path)))
        conditions = [c.value for c in result.config if c.kind == "Router" and c.name == "condition"]
        if conditions and result.payloads:
            result.payloads = [
                Payload(f"{payload.scenario}; route: {condition}", payload.format, payload.body, payload.source)
                for payload in result.payloads
                for condition in conditions
            ]
            result.assumptions.append("Route payload bodies are schema-valid templates; values used in XPath/non-body conditions may require manual adjustment.")

    def _schema_element(self, path: Path) -> ET.Element:
        root = ET.parse(path).getroot()
        if local_name(root.tag).lower() == "schema" and root.tag.startswith(f"{{{self.XML_SCHEMA}}}"):
            return root
        schemas = [element for element in root.iter() if element.tag == f"{{{self.XML_SCHEMA}}}schema"]
        if not schemas:
            raise ValueError("no embedded XML Schema found")
        return next((schema for schema in schemas if schema.findall(f"{{{self.XML_SCHEMA}}}element")), schemas[0])

    def _schema_role(self, path: Path, root_name: str) -> str:
        mapped = self.schema_message_roles.get((path.name, root_name))
        if mapped:
            return mapped
        haystack = f"{path.stem} {root_name}".lower()
        if re.search(r"(?:^|[_\-. ])(?:source|src|request|req)(?:[_\-. ]|$)", haystack):
            return "Source"
        if re.search(r"(?:^|[_\-. ])(?:target|tgt|response|resp|res)(?:[_\-. ]|$)", haystack):
            return "Target"
        return "Message"

    def _sample_xsd(self, path: Path) -> str:
        schema = self._schema_element(path)
        ns = {"xs": self.XML_SCHEMA}
        elements = schema.findall("xs:element", ns)
        global_types = {x.attrib["name"]: x for x in schema.findall("xs:complexType", ns) if "name" in x.attrib}
        if not elements:
            if not global_types:
                raise ValueError("no global xs:element or named xs:complexType")
            inferred_type = next(iter(global_types))
            elements = [ET.Element(f"{{{self.XML_SCHEMA}}}element", {"name": inferred_type, "type": inferred_type})]
        target_ns = schema.attrib.get("targetNamespace")

        def scalar_value(type_name: str, node: ET.Element) -> str:
            enum = node.find(".//xs:enumeration", ns)
            if enum is not None:
                return enum.attrib.get("value", "")
            field_name = (node.attrib.get("name") or node.attrib.get("ref", "").split(":")[-1]).lower()
            samples = {
                "filename": "ACKFILE0001PSR.xml",
                "unique_ref_no": "TEST-REF-001",
                "gif_no": "TEST-GIF-001",
                "gcif_no": "TEST-GCIF-001",
                "payment_ref_no": "TEST-PAY-001",
                "status": "SUCCESS",
                "status1": "ACCEPTED",
                "status_description": "Synthetic successful acknowledgement",
                "detail": "Synthetic test record",
                "original_filename": "ACKFILE0001PSR.xml",
            }
            if field_name in samples:
                return samples[field_name]
            lowered = type_name.lower()
            if "boolean" in lowered:
                return "true"
            if any(x in lowered for x in ("int", "decimal", "double", "float", "long", "short")):
                return "1"
            if "dateTime".lower() in lowered:
                return "2026-01-15T10:30:00Z"
            if "date" in lowered:
                return "2026-01-15"
            return "TEST-VALUE"

        def build(declaration: ET.Element, is_root: bool = False) -> ET.Element:
            name = declaration.attrib.get("name") or declaration.attrib.get("ref", "item").split(":")[-1]
            tag = f"{{{target_ns}}}{name}" if target_ns else name
            output = ET.Element(tag)
            type_name = declaration.attrib.get("type", "").split(":")[-1]
            complex_type = declaration.find("xs:complexType", ns)
            if complex_type is None:
                complex_type = global_types.get(type_name)
            if complex_type is None:
                output.text = scalar_value(type_name, declaration)
                return output
            demonstrate_optional_children = declaration.attrib.get("maxOccurs", "1") != "1"
            for attribute in complex_type.findall(".//xs:attribute", ns):
                if attribute.attrib.get("use") == "required":
                    output.set(attribute.attrib.get("name", "attribute"), scalar_value(attribute.attrib.get("type", "string"), attribute))
            container = next((complex_type.find(f"xs:{kind}", ns) for kind in ("sequence", "all", "choice") if complex_type.find(f"xs:{kind}", ns) is not None), None)
            if container is not None:
                children = container.findall("xs:element", ns)
                if local_name(container.tag) == "choice":
                    children = children[:1]
                for child in children:
                    if (
                        child.attrib.get("minOccurs", "1") == "0"
                        and child.attrib.get("maxOccurs", "1") == "1"
                        and not demonstrate_optional_children
                    ):
                        continue
                    output.append(build(child))
            return output

        root = build(elements[0], True)
        if target_ns:
            ET.register_namespace("", target_ns)
        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="unicode", xml_declaration=False)

    def _sample_json(self, schema: dict, root: dict) -> object:
        if "$ref" in schema:
            target: object = root
            for part in schema["$ref"].removeprefix("#/").split("/"):
                target = target[part]  # type: ignore[index]
            return self._sample_json(target, root)  # type: ignore[arg-type]
        if "enum" in schema:
            return schema["enum"][0]
        schema_type = schema.get("type")
        if schema_type == "object" or "properties" in schema:
            required = set(schema.get("required", []))
            return {key: self._sample_json(value, root) for key, value in schema.get("properties", {}).items() if key in required or not required}
        if schema_type == "array":
            return [self._sample_json(schema.get("items", {}), root)]
        if schema_type in {"number", "integer"}:
            return schema.get("minimum", 1)
        if schema_type == "boolean":
            return True
        if schema.get("format") == "date-time":
            return "2026-01-15T10:30:00Z"
        if schema.get("format") == "date":
            return "2026-01-15"
        return schema.get("example", "TEST-VALUE")

    def _deduplicate(self, result: Analysis) -> None:
        result.steps = list(dict.fromkeys(result.steps))
        result.config = list({(x.step, x.kind, x.action, x.name, x.value): x for x in result.config}.values())
        for attr in ("headers", "properties"):
            items: list[Requirement] = getattr(result, attr)
            merged: dict[str, Requirement] = {}
            for item in items:
                if item.name not in merged or item.mandatory:
                    merged[item.name] = item
            setattr(result, attr, list(merged.values()))
