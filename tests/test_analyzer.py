from pathlib import Path
import tempfile
import unittest
import zipfile

from iflow_testpayload import IFlowAnalyzer


FIXTURE = Path(__file__).parent / "fixtures" / "order_iflow"


class AnalyzerTests(unittest.TestCase):
    def test_directory_analysis_generates_report(self):
        analysis = IFlowAnalyzer(FIXTURE).analyze()
        report = analysis.to_markdown()
        self.assertEqual(analysis.name, "Order Intake")
        self.assertEqual(analysis.sender, "HTTP")
        self.assertEqual(analysis.receiver, "SFTP")
        self.assertIn("X-Customer-ID", report)
        self.assertIn("X-Correlation-ID", report)
        self.assertIn("| Environment | TEST | Yes |", report)
        self.assertIn("<Order xmlns=\"urn:test:order\">", report)
        self.assertIn("<Amount>1</Amount>", report)
        self.assertNotIn("<Note>", report)
        self.assertIn("route: ${property.Environment} = 'TEST'", report)

    def test_zip_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "order.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for path in FIXTURE.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(FIXTURE))
            analysis = IFlowAnalyzer(archive_path).analyze()
            self.assertTrue(analysis.payloads)
            self.assertIn("Order.iflw", analysis.inventory["IFlow definitions"][0])

    def test_rejects_non_zip_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt") as handle:
            with self.assertRaisesRegex(Exception, "directory or .zip"):
                IFlowAnalyzer(Path(handle.name)).analyze()

    def test_type_only_schema_is_supporting_when_document_schema_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "DT_Test.xsd").write_text(
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:test">'
                '<xs:complexType name="DT_Test"><xs:sequence><xs:element name="Record" minOccurs="0" maxOccurs="unbounded">'
                '<xs:complexType><xs:sequence><xs:element name="Value" type="xs:string" minOccurs="0"/>'
                '</xs:sequence></xs:complexType></xs:element></xs:sequence></xs:complexType></xs:schema>',
                encoding="utf-8",
            )
            (root / "MT_Test.xsd").write_text(
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:test">'
                '<xs:element name="MT_Test" type="DT_Test"/><xs:complexType name="DT_Test"><xs:sequence>'
                '<xs:element name="Record" minOccurs="0" maxOccurs="unbounded"><xs:complexType><xs:sequence>'
                '<xs:element name="Value" type="xs:string" minOccurs="0"/></xs:sequence></xs:complexType></xs:element>'
                '</xs:sequence></xs:complexType></xs:schema>',
                encoding="utf-8",
            )
            analysis = IFlowAnalyzer(root).analyze()
            self.assertEqual(len(analysis.payloads), 1)
            self.assertIn("<MT_Test", analysis.payloads[0].body)
            self.assertIn("<Value>TEST-VALUE</Value>", analysis.payloads[0].body)
            self.assertNotIn("no global xs:element", analysis.to_markdown())

    def test_ignores_macos_archive_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / "__MACOSX" / "flow"
            metadata.mkdir(parents=True)
            (metadata / "._Broken.iflw").write_bytes(b"\x00\x05binary metadata")
            schema_dir = root / "project" / "src/main/resources/xsd"
            schema_dir.mkdir(parents=True)
            (schema_dir / "sample.xsd").write_text(
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="Sample" type="xs:string"/></xs:schema>',
                encoding="utf-8",
            )
            analysis = IFlowAnalyzer(root).analyze()
            self.assertEqual(len(analysis.payloads), 1)

    def test_resolves_dynamic_mapping_property_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping_dir = root / "src/main/resources/mapping"
            script_dir = root / "src/main/resources/script"
            mapping_dir.mkdir(parents=True)
            script_dir.mkdir(parents=True)
            (mapping_dir / "sample.mmap").write_text(
                '<mapping><link>getProperty.groovy</link><brick fname="getProperty"><arg><brick fname="const">'
                '<bindings><param name="value"><value>CamelFileExchangeFile</value></param></bindings>'
                '</brick></arg></brick></mapping>',
                encoding="utf-8",
            )
            (script_dir / "getProperty.groovy").write_text(
                'def String getProperty(String propertyName, MappingContext context) { return context.getProperty(propertyName) }',
                encoding="utf-8",
            )
            analysis = IFlowAnalyzer(root).analyze()
            requirement = next(item for item in analysis.properties if item.name == "CamelFileExchangeFile")
            self.assertEqual(requirement.sample, "ACKFILE0001PSR.xml")
            self.assertNotIn("exact property cannot be enumerated", analysis.to_markdown())

    def test_generates_xml_from_wsdl_embedded_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Order_Source.wsdl").write_text(
                '<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" '
                'xmlns:xs="http://www.w3.org/2001/XMLSchema"><wsdl:types>'
                '<xs:schema targetNamespace="urn:wsdl:test"><xs:element name="OrderRequest">'
                '<xs:complexType><xs:sequence><xs:element name="OrderId" type="xs:string"/>'
                '</xs:sequence></xs:complexType></xs:element></xs:schema>'
                '</wsdl:types></wsdl:definitions>',
                encoding="utf-8",
            )
            analysis = IFlowAnalyzer(root).analyze()
            self.assertEqual(len(analysis.payloads), 1)
            self.assertEqual(analysis.payloads[0].format, "xml")
            self.assertIn("Source happy path", analysis.payloads[0].scenario)
            self.assertIn("<OrderRequest", analysis.payloads[0].body)


if __name__ == "__main__":
    unittest.main()
