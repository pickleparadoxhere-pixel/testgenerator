#!/usr/bin/env python3
import sys
import os
import argparse
import json
import logging

# Ensure root workspace directory is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services.iflow_parser import IFlowParser
from backend.services.ai_test_generator import AITestGenerator
from backend.services.cpi_runner import CPITestRunner
from backend.models.schema import TestSuiteGenerationRequest, TestExecutionRequest
from backend.samples.sample_iflow import create_sample_iflow_zip

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def main():
    parser = argparse.ArgumentParser(description="SAP Integration Suite (CPI) AI Test CLI Engine")
    parser.add_argument("--iflow", help="Path to local iFlow .zip archive (default: uses sample iFlow)")
    parser.add_argument("--cpi-endpoint", default="http://localhost:10000/mock/simulated_cpi_inbound", help="CPI runtime endpoint URL")
    parser.add_argument("--export-junit", help="Output path for JUnit XML report (e.g. junit-report.xml)")
    parser.add_argument("--gemini-key", help="Google Gemini API Key for AI synthesis")

    args = parser.parse_args()

    print("================================================================")
    print(" 🧪 SAP Integration Suite AI Test & Mock CLI Agent")
    print("================================================================")

    # 1. Load or generate iFlow ZIP
    if args.iflow and os.path.exists(args.iflow):
        print(f"📦 Loading iFlow archive: {args.iflow}")
        with open(args.iflow, "rb") as f:
            zip_bytes = f.read()
        filename = os.path.basename(args.iflow)
    else:
        print("📦 No iFlow file specified. Generating standard sample iFlow (SalesOrder_S4HANA_Creation.zip)...")
        zip_bytes = create_sample_iflow_zip()
        filename = "SalesOrder_S4HANA_Creation.zip"

    # 2. Parse iFlow
    iflow_parser = IFlowParser()
    metadata = iflow_parser.parse_zip(zip_bytes, filename)
    print(f"✅ Successfully parsed iFlow: {metadata.name} (ID: {metadata.id})")
    print(f"   - Inbound Endpoint: {metadata.inbound_endpoint.adapter_type} -> {metadata.inbound_endpoint.url_path}")
    print(f"   - Receiver Systems to Mock: {', '.join([r.name for r in metadata.receiver_endpoints])}")

    # 3. Generate Test Suite
    print("\n🤖 Synthesizing AI Test Cases...")
    ai_generator = AITestGenerator(api_key=args.gemini_key)
    gen_request = TestSuiteGenerationRequest(iflow_metadata=metadata, num_cases_per_category=1)
    test_cases = ai_generator.generate_test_suite(gen_request)

    print(f"✅ Synthesized {len(test_cases)} Test Cases:")
    for tc in test_cases:
        print(f"   - [{tc.category.upper()}] {tc.id}: {tc.name}")

    # 4. Run Test Suite
    print(f"\n⚡ Executing Test Cases against endpoint: {args.cpi_endpoint}...")
    exec_request = TestExecutionRequest(
        cpi_endpoint=args.cpi_endpoint,
        test_cases=test_cases,
        enable_mpl_check=True
    )
    runner = CPITestRunner(exec_request)
    report = runner.execute_suite()

    print("\n================================================================")
    print(f" 📊 TEST RESULTS SUMMARY")
    print("================================================================")
    print(f" Total Executed: {report.total_tests}")
    print(f" Passed:         {report.passed} ✅")
    print(f" Failed:         {report.failed} ❌")
    print(f" Total Duration: {report.duration_ms} ms")
    print("----------------------------------------------------------------")

    for res in report.results:
        status_icon = "✅ PASS" if res.status == "PASS" else "❌ FAIL"
        print(f" {status_icon} | [{res.category.upper()}] {res.test_id}: {res.name} ({res.execution_time_ms} ms)")

    # 5. Export JUnit XML if requested
    if args.export_junit and report.junit_xml:
        with open(args.export_junit, "w", encoding="utf-8") as f:
            f.write(report.junit_xml)
        print(f"\n📄 Saved JUnit XML test report to: {args.export_junit}")

    if report.failed > 0:
        print("\n❌ CI/CD Pipeline Status: FAILED (Unresolved test failures found)")
        sys.exit(1)
    else:
        print("\n✅ CI/CD Pipeline Status: PASSED (Ready for Transport)")
        sys.exit(0)

if __name__ == "__main__":
    main()
