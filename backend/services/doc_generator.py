import os
import io
import re
import sys
import datetime
import logging
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import ssl
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)

HAS_DOCX = False
Document = None
Inches = None
Pt = None
RGBColor = None
WD_ALIGN_PARAGRAPH = None
WD_TABLE_ALIGNMENT = None
parse_xml = None
nsdecls = None

def ensure_docx_installed() -> bool:
    global HAS_DOCX, Document, Inches, Pt, RGBColor, WD_ALIGN_PARAGRAPH, WD_TABLE_ALIGNMENT, parse_xml, nsdecls
    if HAS_DOCX:
        return True
    try:
        import docx
        from docx import Document as _Document
        from docx.shared import Inches as _Inches, Pt as _Pt, RGBColor as _RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH as _WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_TABLE_ALIGNMENT as _WD_TABLE_ALIGNMENT
        from docx.oxml import parse_xml as _parse_xml
        from docx.oxml.ns import nsdecls as _nsdecls

        Document = _Document
        Inches = _Inches
        Pt = _Pt
        RGBColor = _RGBColor
        WD_ALIGN_PARAGRAPH = _WD_ALIGN_PARAGRAPH
        WD_TABLE_ALIGNMENT = _WD_TABLE_ALIGNMENT
        parse_xml = _parse_xml
        nsdecls = _nsdecls

        HAS_DOCX = True
        return True
    except ImportError:
        logger.info("python-docx missing. Attempting runtime auto-installation via pip...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "python-docx"])
            import docx
            from docx import Document as _Document
            from docx.shared import Inches as _Inches, Pt as _Pt, RGBColor as _RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH as _WD_ALIGN_PARAGRAPH
            from docx.enum.table import WD_TABLE_ALIGNMENT as _WD_TABLE_ALIGNMENT
            from docx.oxml import parse_xml as _parse_xml
            from docx.oxml.ns import nsdecls as _nsdecls

            Document = _Document
            Inches = _Inches
            Pt = _Pt
            RGBColor = _RGBColor
            WD_ALIGN_PARAGRAPH = _WD_ALIGN_PARAGRAPH
            WD_TABLE_ALIGNMENT = _WD_TABLE_ALIGNMENT
            parse_xml = _parse_xml
            nsdecls = _nsdecls

            HAS_DOCX = True
            logger.info("python-docx successfully installed at runtime!")
            return True
        except Exception as e:
            logger.error(f"Runtime auto-install of python-docx failed: {e}")
            return False

def replace_text_in_paragraph(paragraph, old_text: str, new_text: str) -> bool:
    """Replaces text in a paragraph, handling placeholders split across multiple XML runs."""
    if old_text not in paragraph.text:
        return False
    for run in paragraph.runs:
        if old_text in run.text:
            run.text = run.text.replace(old_text, new_text)
            return True
    full_text = paragraph.text.replace(old_text, new_text)
    if paragraph.runs:
        paragraph.runs[0].text = full_text
        for run in paragraph.runs[1:]:
            run.text = ""
        return True
    return False

class TechSpecGenerator:
    """Generates professional AI-powered SAP CPI Technical Specification Word (.docx) documents."""

    NAVY_HEX = "0B2545"
    BLUE_HEX = "134074"
    LIGHT_BG_HEX = "F4F6F9"
    CODE_BG_HEX = "07101D"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")

    def generate_tech_spec(
        self,
        analysis_data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        reference_docx_bytes: Optional[bytes] = None
    ) -> bytes:
        if not ensure_docx_installed():
            raise RuntimeError("python-docx package is not available and could not be installed automatically.")

        iflow_name = analysis_data.get("name") or (metadata and metadata.get("name")) or "SAP iFlow"
        iflow_id = (metadata and metadata.get("id")) or iflow_name

        # Extract text from reference document if provided
        reference_text = ""
        if reference_docx_bytes and len(reference_docx_bytes) > 200:
            try:
                ref_doc = Document(io.BytesIO(reference_docx_bytes))
                ref_paras = [p.text for p in ref_doc.paragraphs if p.text.strip()]
                reference_text = "\n".join(ref_paras[:40])
            except Exception as ex_ref:
                logger.warning(f"Could not extract reference text: {ex_ref}")

        # Synthesize AI Technical Content
        ai_content = self._generate_ai_spec_content(analysis_data, metadata, reference_text)

        if reference_docx_bytes and len(reference_docx_bytes) > 200:
            try:
                return self._fill_reference_template(reference_docx_bytes, analysis_data, metadata, ai_content)
            except Exception as e:
                logger.error(f"Failed to fill reference docx template: {e}. Falling back to standard docx synthesis.", exc_info=True)

        return self._build_standard_tech_spec(analysis_data, metadata, iflow_name, iflow_id, ai_content)

    def _generate_ai_spec_content(
        self,
        analysis: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
        reference_text: str = ""
    ) -> Dict[str, Any]:
        iflow_name = analysis.get("name") or (metadata and metadata.get("name")) or "iFlow"
        iflow_id = (metadata and metadata.get("id")) or iflow_name
        sender = analysis.get("sender") or "HTTPS Sender Adapter"
        receiver = analysis.get("receiver") or "Target Receiver System"
        steps = ", ".join(analysis.get("steps", []))

        # Default rule-based content
        default_content = {
            "summary": (
                f"This technical specification defines the integration architecture, execution flow, data mapping rules, "
                f"and test payloads for the SAP Integration Suite iFlow '{iflow_name}' (ID: {iflow_id}). "
                f"The interface receives inbound messages from '{sender}' and routes processed payloads to '{receiver}'."
            ),
            "architecture": (
                f"The interface follows an asynchronous/synchronous enterprise message exchange pattern. "
                f"Inbound requests arrive at the HTTPS sender adapter, undergo payload validation and Groovy transformation, "
                f"and execute message mappings before delivering to target receiver endpoints."
            ),
            "error_handling": (
                f"Exceptions occurring during message processing are captured by the SAP CPI Exception Subprocess. "
                f"The system logs an SAP Message Processing Log ID (MPL ID) for end-to-end trace logging and observability."
            )
        }

        if not self.api_key:
            return default_content

        # Call Gemini AI for rich architectural content synthesis
        try:
            prompt = f"""
You are a Lead SAP CPI Integration Architect.
Generate technical specification documentation content for the following SAP CPI iFlow artifact:

iFlow Name: {iflow_name} (ID: {iflow_id})
Sender System: {sender}
Receiver Systems: {receiver}
Flow Sequence Steps: {steps}

Reference Template Context:
{reference_text or 'Standard SAP Integration Suite Technical Specification Template'}

Return a clean JSON object containing:
{{
  "summary": "Detailed 3-sentence executive summary describing business value and technical purpose.",
  "architecture": "Detailed paragraph describing message exchange patterns, transformation logic, and receiver routing.",
  "error_handling": "Paragraph describing exception handling, SAP MPL ID logging, and alerting rules."
}}
"""
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
            payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                text_out = resp_data["candidates"][0]["content"]["parts"][0]["text"]
                
                json_match = re.search(r"\{.*\}", text_out, re.DOTALL)
                if json_match:
                    ai_json = json.loads(json_match.group(0))
                    return {
                        "summary": ai_json.get("summary", default_content["summary"]),
                        "architecture": ai_json.get("architecture", default_content["architecture"]),
                        "error_handling": ai_json.get("error_handling", default_content["error_handling"])
                    }
        except Exception as ex_ai:
            logger.warning(f"Gemini AI spec synthesis note: {ex_ai}. Using rule-based spec content.")

        return default_content

    def _set_cell_background(self, cell, fill_hex: str):
        tcPr = cell._tc.get_or_add_tcPr()
        shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
        tcPr.append(shd)

    def _fill_reference_template(
        self,
        reference_bytes: bytes,
        analysis: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
        ai_content: Dict[str, Any]
    ) -> bytes:
        doc = Document(io.BytesIO(reference_bytes))
        iflow_name = analysis.get("name") or (metadata and metadata.get("name")) or "iFlow"
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
            "{{SUMMARY}}": ai_content.get("summary", ""),
            "{{ARCHITECTURE}}": ai_content.get("architecture", ""),
            "{{ERROR_HANDLING}}": ai_content.get("error_handling", ""),
            "{{TIMESTAMP}}": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # 1. Replace placeholder text in all paragraphs (handles split XML runs!)
        for p in doc.paragraphs:
            for key, val in replacements.items():
                replace_text_in_paragraph(p, key, val)

        # 2. Replace placeholder text in all tables
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for key, val in replacements.items():
                            replace_text_in_paragraph(p, key, val)

        # 3. Append complete extracted iFlow technical specification sections
        doc.add_heading(f"Extracted Technical Specifications for '{iflow_name}' (ID: {iflow_id})", level=1)
        
        p_desc = doc.add_paragraph()
        p_desc.add_run(ai_content.get("summary", ""))

        doc.add_heading("Architecture & Error Handling", level=2)
        doc.add_paragraph(ai_content.get("architecture", ""))
        doc.add_paragraph(ai_content.get("error_handling", ""))

        steps = analysis.get("steps", [])
        if steps:
            doc.add_heading("Execution Steps Sequence", level=2)
            for i, step in enumerate(steps, 1):
                doc.add_paragraph(f"{i}. {step}")

        doc.add_heading("Extracted Configuration Mapping Table", level=2)
        self._add_config_table(doc, analysis.get("config", []))

        doc.add_heading("Required HTTP Headers", level=2)
        self._add_requirements_table(doc, "Required HTTP Headers", analysis.get("headers", []))

        doc.add_heading("Required Exchange Properties", level=2)
        self._add_requirements_table(doc, "Exchange Properties", analysis.get("properties", []))

        doc.add_heading("Schema-Derived Test Payloads", level=2)
        self._add_payloads_section(doc, analysis.get("payloads", []))

        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def _build_standard_tech_spec(
        self,
        analysis: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
        iflow_name: str,
        iflow_id: str,
        ai_content: Dict[str, Any]
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
            self._set_cell_background(r_cells[0], self.LIGHT_BG_HEX)
            for p in r_cells[0].paragraphs:
                for r in p.runs:
                    r.font.bold = True

        doc.add_paragraph().paragraph_format.space_after = Pt(12)

        # 1. Executive Summary
        doc.add_heading("1. Executive Summary", level=1)
        doc.add_paragraph(ai_content.get("summary", ""))

        # Package Inventory
        inventory = analysis.get("inventory", {})
        if inventory:
            p_inv = doc.add_paragraph()
            p_inv.add_run("Package Artifact Inventory:").bold = True
            for kind, files in inventory.items():
                if files:
                    doc.add_paragraph(f"• {kind}: " + ", ".join(files), style='List Bullet')

        # 2. Interface Architecture & Sequence
        doc.add_heading("2. Interface Architecture & Flow Sequence", level=1)
        doc.add_paragraph(ai_content.get("architecture", ""))
        doc.add_paragraph(f"• Sender System: {analysis.get('sender', 'HTTPS Sender Adapter')}")
        doc.add_paragraph(f"• Receiver Systems: {analysis.get('receiver', 'Backend Target System')}")

        steps = analysis.get("steps", [])
        if steps:
            doc.add_paragraph("Execution Steps Sequence:").bold = True
            for i, step in enumerate(steps, 1):
                doc.add_paragraph(f"{i}. {step}")

        # 3. Exception Handling
        doc.add_heading("3. Exception Handling & Logging", level=1)
        doc.add_paragraph(ai_content.get("error_handling", ""))

        # 4. Configuration Table
        doc.add_heading("4. Extracted Configuration Table", level=1)
        self._add_config_table(doc, analysis.get("config", []))

        # 5. Required Headers
        doc.add_heading("5. Required HTTP Headers", level=1)
        self._add_requirements_table(doc, "Required HTTP Headers", analysis.get("headers", []))

        # 6. Exchange Properties
        doc.add_heading("6. Required Exchange Properties", level=1)
        self._add_requirements_table(doc, "Exchange Properties", analysis.get("properties", []))

        # 7. Test Payloads Section
        doc.add_heading("7. Schema-Derived Test Payloads", level=1)
        self._add_payloads_section(doc, analysis.get("payloads", []))

        # 8. Assumptions & Gaps
        assumptions = analysis.get("assumptions", [])
        if assumptions:
            doc.add_heading("8. Assumptions & Technical Gaps", level=1)
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
            self._set_cell_background(hdr_cells[i], self.NAVY_HEX)
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
            self._set_cell_background(hdr_cells[i], self.BLUE_HEX)
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

            p_code = doc.add_paragraph()
            p_code.paragraph_format.space_before = Pt(4)
            p_code.paragraph_format.space_after = Pt(12)
            run_code = p_code.add_run(body)
            run_code.font.name = "Consolas"
            run_code.font.size = Pt(9.5)
