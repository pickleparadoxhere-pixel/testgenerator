import io
import re
import datetime
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import docx
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False
    logger.warning("python-docx is not installed. Docx generation will return a fallback warning.")

def set_cell_background(cell, fill_hex: str):
    if not HAS_DOCX:
        return
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
    tcPr.append(shd)

class TechSpecGenerator:
    """Generates professional SAP CPI Technical Specification Word (.docx) documents."""

    NAVY_HEX = "0B2545"
    BLUE_HEX = "134074"
    LIGHT_BG_HEX = "F4F6F9"
    CODE_BG_HEX = "07101D"

    def generate_tech_spec(
        self,
        analysis_data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        reference_docx_bytes: Optional[bytes] = None
    ) -> bytes:
        if not HAS_DOCX:
            raise RuntimeError("python-docx is not installed on the server environment. Please run 'pip install python-docx'.")

        iflow_name = analysis_data.get("name") or (metadata and metadata.get("name")) or "SAP iFlow"
        iflow_id = (metadata and metadata.get("id")) or iflow_name

        if reference_docx_bytes and len(reference_docx_bytes) > 500:
            try:
                return self._fill_reference_template(reference_docx_bytes, analysis_data, metadata)
            except Exception as e:
                logger.error(f"Failed to fill reference docx template: {e}. Falling back to standard docx synthesis.", exc_info=True)

        return self._build_standard_tech_spec(analysis_data, metadata, iflow_name, iflow_id)

    def _fill_reference_template(
        self,
        reference_bytes: bytes,
        analysis: Dict[str, Any],
        metadata: Optional[Dict[str, Any]]
    ) -> bytes:
        doc = Document(io.BytesIO(reference_bytes))
        iflow_name = analysis.get("name") or "iFlow"
        iflow_id = (metadata and metadata.get("id")) or iflow_name
        sender = analysis.get("sender") or "HTTPS Sender"
        receiver = analysis.get("receiver") or "Receiver System"
        today_str = datetime.date.today().strftime("%B %d, %Y")

        replacements = {
            "{{IFLOW_NAME}}": iflow_name,
            "{{IFLOW_ID}}": iflow_id,
            "{{SENDER}}": sender,
            "{{RECEIVER}}": receiver,
            "{{DATE}}": today_str,
            "{{TIMESTAMP}}": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # 1. Replace placeholder text in all paragraphs
        for p in doc.paragraphs:
            for key, val in replacements.items():
                if key in p.text:
                    p.text = p.text.replace(key, val)

        # 2. Replace placeholder text in all tables
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for key, val in replacements.items():
                            if key in p.text:
                                p.text = p.text.replace(key, val)

        # 3. Append extracted dynamic analysis sections if not already present
        doc.add_heading(f"Extracted Configuration & Test Payloads for {iflow_name}", level=1)
        self._add_config_table(doc, analysis.get("config", []))
        self._add_requirements_table(doc, "Required HTTP Headers", analysis.get("headers", []))
        self._add_requirements_table(doc, "Required Exchange Properties", analysis.get("properties", []))
        self._add_payloads_section(doc, analysis.get("payloads", []))

        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def _build_standard_tech_spec(
        self,
        analysis: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
        iflow_name: str,
        iflow_id: str
    ) -> bytes:
        doc = Document()

        # Page Margins
        for section in doc.sections:
            section.top_margin = Inches(1.0)
            section.bottom_margin = Inches(1.0)
            section.left_margin = Inches(1.0)
            section.right_margin = Inches(1.0)

        # Title Banner
        p_title = doc.add_paragraph()
        p_title.paragraph_format.space_before = Pt(0)
        p_title.paragraph_format.space_after = Pt(4)
        run_title = p_title.add_run("SAP CPI Technical Specification Document")
        run_title.font.name = "Calibri"
        run_title.font.size = Pt(24)
        run_title.font.bold = True
        run_title.font.color.rgb = RGBColor(11, 37, 69)

        # Subtitle
        p_sub = doc.add_paragraph()
        p_sub.paragraph_format.space_after = Pt(18)
        run_sub = p_sub.add_run(f"Integration Artifact: {iflow_name} (ID: {iflow_id})")
        run_sub.font.name = "Calibri"
        run_sub.font.size = Pt(14)
        run_sub.font.color.rgb = RGBColor(19, 64, 116)

        # Document Metadata Table
        meta_table = doc.add_table(rows=4, cols=2)
        meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        meta_table.style = 'Table Grid'
        
        meta_data = [
            ("Technical Artifact ID", iflow_id),
            ("Integration Name", iflow_name),
            ("Document Version / Date", f"1.0.0 — {datetime.date.today().strftime('%B %d, %Y')}"),
            ("Status", "Approved Technical Specification")
        ]
        for row_idx, (k, v) in enumerate(meta_data):
            r_cells = meta_table.rows[row_idx].cells
            r_cells[0].text = k
            r_cells[1].text = v
            set_cell_background(r_cells[0], self.LIGHT_BG_HEX)
            for p in r_cells[0].paragraphs:
                for r in p.runs:
                    r.font.bold = True

        doc.add_paragraph().paragraph_format.space_after = Pt(12)

        # 1. Executive Summary
        doc.add_heading("1. Executive Summary", level=1)
        doc.add_paragraph(
            f"This technical specification provides the complete design, extracted flow sequence, "
            f"configuration mapping tables, required headers, and test payloads for the SAP Integration Suite "
            f"iFlow '{iflow_name}'. All specifications are extracted directly from authoritative design-time artifacts."
        )

        # Package Inventory
        inventory = analysis.get("inventory", {})
        if inventory:
            p_inv = doc.add_paragraph()
            p_inv.add_run("Package Artifact Inventory:").bold = True
            for kind, files in inventory.items():
                if files:
                    doc.add_paragraph(f"• {kind}: " + ", ".join(files), style='List Bullet')

        # 2. Sender & Receiver Systems
        doc.add_heading("2. Interface Architecture & Flow Sequence", level=1)
        doc.add_paragraph(f"• Sender System: {analysis.get('sender', 'HTTPS Sender Adapter')}")
        doc.add_paragraph(f"• Receiver Systems: {analysis.get('receiver', 'Backend Target System')}")

        steps = analysis.get("steps", [])
        if steps:
            doc.add_paragraph("Execution Steps Sequence:").bold = True
            for i, step in enumerate(steps, 1):
                doc.add_paragraph(f"{i}. {step}")

        # 3. Configuration Table
        doc.add_heading("3. Extracted Configuration Table", level=1)
        self._add_config_table(doc, analysis.get("config", []))

        # 4. Required Headers
        doc.add_heading("4. Required HTTP Headers", level=1)
        self._add_requirements_table(doc, "Required HTTP Headers", analysis.get("headers", []))

        # 5. Exchange Properties
        doc.add_heading("5. Required Exchange Properties", level=1)
        self._add_requirements_table(doc, "Exchange Properties", analysis.get("properties", []))

        # 6. Test Payloads Section
        doc.add_heading("6. Schema-Derived Test Payloads", level=1)
        self._add_payloads_section(doc, analysis.get("payloads", []))

        # 7. Assumptions & Gaps
        assumptions = analysis.get("assumptions", [])
        if assumptions:
            doc.add_heading("7. Assumptions & Technical Gaps", level=1)
            for ass in assumptions:
                doc.add_paragraph(f"• {ass}", style='List Bullet')

        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def _add_config_table(self, doc, configs: List[Dict[str, Any]]):
        if not configs:
            doc.add_paragraph("No specific configuration steps extracted.")
            return

        table = doc.add_table(rows=1, cols=5)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.style = 'Table Grid'
        
        hdr_cells = table.rows[0].cells
        hdr_titles = ["Step", "Type", "Action", "Name", "Value / Source"]
        for i, title in enumerate(hdr_titles):
            hdr_cells[i].text = title
            set_cell_background(hdr_cells[i], self.NAVY_HEX)
            for p in hdr_cells[i].paragraphs:
                for r in p.runs:
                    r.font.bold = True
                    r.font.color.rgb = RGBColor(255, 255, 255)

        for row_data in configs:
            r_cells = table.add_row().cells
            r_cells[0].text = str(row_data.get("step", ""))
            r_cells[1].text = str(row_data.get("kind", ""))
            r_cells[2].text = str(row_data.get("action", ""))
            r_cells[3].text = str(row_data.get("name", ""))
            r_cells[4].text = str(row_data.get("value", ""))

        doc.add_paragraph().paragraph_format.space_after = Pt(8)

    def _add_requirements_table(self, doc, label: str, reqs: List[Dict[str, Any]]):
        if not reqs:
            doc.add_paragraph(f"No mandatory {label.lower()} detected.")
            return

        table = doc.add_table(rows=1, cols=4)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.style = 'Table Grid'

        hdr_cells = table.rows[0].cells
        hdr_titles = ["Name", "Sample Value", "Mandatory?", "Notes"]
        for i, title in enumerate(hdr_titles):
            hdr_cells[i].text = title
            set_cell_background(hdr_cells[i], self.BLUE_HEX)
            for p in hdr_cells[i].paragraphs:
                for r in p.runs:
                    r.font.bold = True
                    r.font.color.rgb = RGBColor(255, 255, 255)

        for req in reqs:
            r_cells = table.add_row().cells
            r_cells[0].text = str(req.get("name", ""))
            r_cells[1].text = str(req.get("sample", ""))
            r_cells[2].text = "Yes" if req.get("mandatory") else "No"
            r_cells[3].text = str(req.get("notes", ""))

        doc.add_paragraph().paragraph_format.space_after = Pt(8)

    def _add_payloads_section(self, doc, payloads: List[Dict[str, Any]]):
        if not payloads:
            doc.add_paragraph("No schema test payloads generated.")
            return

        for p in payloads:
            scenario = p.get("scenario", "Test Scenario")
            fmt = str(p.get("format", "xml")).upper()
            body = p.get("body", "")
            source = p.get("source", "Schema")

            h3 = doc.add_heading(f"Scenario: {scenario} ({fmt})", level=2)
            doc.add_paragraph(f"Derived from schema: `{source}`")

            # Code Block Box
            p_code = doc.add_paragraph()
            p_code.paragraph_format.space_before = Pt(4)
            p_code.paragraph_format.space_after = Pt(12)
            run_code = p_code.add_run(body)
            run_code.font.name = "Consolas"
            run_code.font.size = Pt(9.5)
