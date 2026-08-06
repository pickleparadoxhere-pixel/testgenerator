// SAP CPI AI Test Agent - Web Studio Client
document.addEventListener("DOMContentLoaded", () => {
    // App State
    let currentIFlow = null;
    let currentTestCases = [];
    let selectedTestCase = null;
    let lastReport = null;

    // UI Elements
    const navTabs = document.querySelectorAll(".nav-tab");
    const tabContents = document.querySelectorAll(".tab-content");
    const btnSampleiFlow = document.getElementById("btnSampleiFlow");
    const btnConnectTenant = document.getElementById("btnConnectTenant");
    const tenantModal = document.getElementById("tenantModal");
    const iflowPickerModal = document.getElementById("iflowPickerModal");
    const iflowPickerList = document.getElementById("iflowPickerList");
    const btnSubmitTenant = document.getElementById("btnSubmitTenant");
    const serviceKeyJson = document.getElementById("serviceKeyJson");
    const rawCurlInput = document.getElementById("rawCurlInput");
    const fileInput = document.getElementById("fileInput");
    const dropZone = document.getElementById("dropZone");
    const btnGenerateTests = document.getElementById("btnGenerateTests");
    const btnRunSuite = document.getElementById("btnRunSuite");
    const btnClearMocks = document.getElementById("btnClearMocks");
    const btnRefreshIntercepts = document.getElementById("btnRefreshIntercepts");
    const btnExportJUnit = document.getElementById("btnExportJUnit");

    // --- Service Key JSON Auto-Fill ---
    if (serviceKeyJson) {
        serviceKeyJson.addEventListener("input", () => {
            const raw = serviceKeyJson.value.trim();
            if (!raw) return;
            try {
                const parsed = JSON.parse(raw);
                const extracted = extractServiceKeyFields(parsed);

                if (extracted.hostUrl && document.getElementById("cpiHost")) document.getElementById("cpiHost").value = extracted.hostUrl;
                if (extracted.clientId && document.getElementById("clientId")) document.getElementById("clientId").value = extracted.clientId;
                if (extracted.clientSecret && document.getElementById("clientSecret")) document.getElementById("clientSecret").value = extracted.clientSecret;
                if (extracted.tokenUrl && document.getElementById("tokenUrl")) document.getElementById("tokenUrl").value = extracted.tokenUrl;
            } catch (e) {
                // Ignore while user is typing incomplete JSON
            }
        });
    }

    function extractServiceKeyFields(keyObj) {
        if (!keyObj || typeof keyObj !== 'object') return {};
        
        let src = keyObj.oauth || keyObj.credentials || keyObj.service_key || keyObj;
        
        let hostUrl = src.url || src.management_url || src.service_url || src.api || '';
        if (!hostUrl && src.endpoints) {
            hostUrl = src.endpoints.api || src.endpoints.url || src.endpoints.web || '';
        }
        
        let clientId = src.clientid || src.client_id || (src.oauth ? (src.oauth.clientid || src.oauth.client_id) : '');
        let clientSecret = src.clientsecret || src.client_secret || (src.oauth ? (src.oauth.clientsecret || src.oauth.client_secret) : '');
        let tokenUrl = src.tokenurl || src.token_url || (src.oauth ? (src.oauth.tokenurl || src.oauth.token_url) : '');

        return { hostUrl, clientId, clientSecret, tokenUrl };
    }

    // --- Tab Navigation ---
    navTabs.forEach(tab => {
        tab.addEventListener("click", () => {
            const target = tab.getAttribute("data-tab");
            navTabs.forEach(t => t.classList.remove("active"));
            tabContents.forEach(c => c.classList.remove("active"));
            tab.classList.add("active");
            document.getElementById(target).classList.add("active");
        });
    });

    function switchTab(tabId) {
        navTabs.forEach(t => {
            if (t.getAttribute("data-tab") === tabId) t.classList.add("active");
            else t.classList.remove("active");
        });
        tabContents.forEach(c => {
            if (c.id === tabId) c.classList.add("active");
            else c.classList.remove("active");
        });
    }

    // --- Load Sample iFlow ---
    btnSampleiFlow.addEventListener("click", async () => {
        btnSampleiFlow.disabled = true;
        btnSampleiFlow.innerText = "Loading...";
        try {
            const res = await fetch("/api/v1/cpi/fetch-iflow/SalesOrder_S4HANA_Creation");
            if (res.ok) {
                currentIFlow = await res.json();
                renderIFlowMetadata(currentIFlow);
            }
        } catch (err) {
            alert("Error loading sample iFlow: " + err.message);
        } finally {
            btnSampleiFlow.disabled = false;
            btnSampleiFlow.innerHTML = "<span>📦</span> Load Sample iFlow";
        }
    });

    // --- File Drag & Drop Upload ---
    dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.classList.add("dragover");
    });

    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));

    dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("dragover");
        if (e.dataTransfer.files.length) {
            handleFileUpload(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener("change", (e) => {
        if (e.target.files.length) {
            handleFileUpload(e.target.files[0]);
        }
    });

    async function handleFileUpload(file) {
        if (!file.name.endsWith(".zip")) {
            alert("Please upload an iFlow .zip bundle");
            return;
        }

        const formData = new FormData();
        formData.append("file", file);

        try {
            const res = await fetch("/api/v1/iflow/parse", {
                method: "POST",
                body: formData
            });
            if (res.ok) {
                currentIFlow = await res.json();
                renderIFlowMetadata(currentIFlow);
            } else {
                const err = await res.json();
                alert("Upload failed: " + (err.detail || err.error));
            }
        } catch (err) {
            alert("Upload error: " + err.message);
        }
    }

    // --- Render iFlow Metadata ---
    function renderIFlowMetadata(metadata) {
        document.getElementById("iflowStatusBadge").className = "badge status-badge";
        document.getElementById("iflowStatusBadge").innerText = "Active iFlow Loaded";

        const receiversHtml = metadata.receiver_endpoints.map(r => 
            `<span class="badge" style="background: rgba(139, 92, 246, 0.15); border-color: var(--accent-purple);">
                ${r.name} (${r.adapter_type})
             </span>`
        ).join(" ");

        const scriptsHtml = metadata.groovy_scripts.map(s => `<code>${s}</code>`).join(", ") || "None";

        document.getElementById("metadataBody").innerHTML = `
            <div style="display: flex; flex-direction: column; gap: 1rem;">
                <div>
                    <h4 style="font-size: 1.1rem; color: #fff; font-weight: 700;">${metadata.name}</h4>
                    <p style="font-size: 0.8rem; color: var(--text-muted);">${metadata.description || ''}</p>
                </div>
                <hr style="border: 0; border-top: 1px solid var(--border-color);" />
                <div>
                    <strong style="font-size: 0.8rem; color: var(--text-muted);">Inbound Sender Endpoint:</strong>
                    <div style="margin-top: 0.3rem;">
                        <code style="color: var(--accent-cyan); font-size: 0.85rem;">[${metadata.inbound_endpoint.adapter_type}] ${metadata.inbound_endpoint.url_path}</code>
                    </div>
                </div>
                <div>
                    <strong style="font-size: 0.8rem; color: var(--text-muted);">Receiver Systems to Mock:</strong>
                    <div style="margin-top: 0.4rem; display: flex; gap: 0.4rem; flex-wrap: wrap;">
                        ${receiversHtml}
                    </div>
                </div>
                <div>
                    <strong style="font-size: 0.8rem; color: var(--text-muted);">Detected Groovy Scripts:</strong>
                    <div style="margin-top: 0.3rem; font-size: 0.8rem;">
                        ${scriptsHtml}
                    </div>
                </div>
            </div>
        `;
    }

    // --- SAP Tenant Modal Connection Handler ---
    btnConnectTenant.addEventListener("click", () => {
        tenantModal.classList.add("active");
        setTimeout(() => {
            if (serviceKeyJson) {
                serviceKeyJson.focus();
                serviceKeyJson.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }, 150);
    });

    btnSubmitTenant.addEventListener("click", async () => {
        const rawJson = serviceKeyJson ? serviceKeyJson.value.trim() : "";
        const rawCurl = rawCurlInput ? rawCurlInput.value.trim() : "";
        
        let tenantUrl = document.getElementById("cpiHost") ? document.getElementById("cpiHost").value.trim() : "";
        let clientId = document.getElementById("clientId") ? document.getElementById("clientId").value.trim() : "";
        let clientSecret = document.getElementById("clientSecret") ? document.getElementById("clientSecret").value.trim() : "";
        let tokenUrl = document.getElementById("tokenUrl") ? document.getElementById("tokenUrl").value.trim() : "";
        let iflowName = document.getElementById("iflowName") ? document.getElementById("iflowName").value.trim() : "Horizon";
        let version = document.getElementById("iflowVersion") ? document.getElementById("iflowVersion").value.trim() : "active";

        // Auto-extract if JSON provided
        if (rawJson) {
            try {
                const parsed = JSON.parse(rawJson);
                const ext = extractServiceKeyFields(parsed);
                if (ext.hostUrl) tenantUrl = ext.hostUrl;
                if (ext.clientId) clientId = ext.clientId;
                if (ext.clientSecret) clientSecret = ext.clientSecret;
                if (ext.tokenUrl) tokenUrl = ext.tokenUrl;
            } catch (e) {
                // Not JSON
            }
        }

        btnSubmitTenant.innerText = "Connecting to SAP Tenant...";
        btnSubmitTenant.disabled = true;

        try {
            const res = await fetch("/api/v1/cpi/connect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    tenant_url: tenantUrl,
                    client_id: clientId,
                    client_secret: clientSecret,
                    token_url: tokenUrl,
                    iflow_name: iflowName,
                    version: version,
                    raw_curl: rawCurl
                })
            });
            const data = await res.json();
            
            if (res.ok && data.status !== "ERROR") {
                tenantModal.classList.remove("active");
                showIFlowPickerModal(data.iflows || [], true);
            } else {
                alert(`❌ Connection Response:\n\n${data.error || 'Connection failed.'}`);
            }
        } catch (err) {
            alert("Network Error: " + err.message);
        } finally {
            btnSubmitTenant.innerText = "Fetch iFlow";
            btnSubmitTenant.disabled = false;
        }
    });

    function showIFlowPickerModal(iflows, isLive) {
        if (!iflows.length) {
            alert("No iFlows found on tenant.");
            return;
        }

        const headerTitle = isLive ? "🟢 Connected Live Tenant - Select iFlow" : "📦 Demo Mode - Select Sample iFlow";
        document.querySelector("#iflowPickerModal h3").innerText = headerTitle;

        iflowPickerList.innerHTML = iflows.map(item => `
            <div class="test-item" style="margin-bottom: 0.5rem; display: flex; justify-content: space-between; align-items: center;" data-id="${item.id}">
                <div>
                    <strong style="color: #fff; font-size: 0.9rem;">${item.name}</strong>
                    <div style="font-size: 0.75rem; color: var(--text-muted);">ID: <code>${item.id}</code> | Version: ${item.version || 'active'} | Package: ${item.package_id || 'Default'}</div>
                </div>
                <button class="btn btn-sm btn-primary select-iflow-btn" data-id="${item.id}">Select iFlow</button>
            </div>
        `).join("");

        iflowPickerModal.classList.add("active");

        document.querySelectorAll(".select-iflow-btn").forEach(btn => {
            btn.addEventListener("click", async () => {
                const iflowId = btn.getAttribute("data-id");
                iflowPickerModal.classList.remove("active");
                btnSampleiFlow.innerText = "Loading iFlow...";
                try {
                    const res = await fetch(`/api/v1/cpi/fetch-iflow/${iflowId}`);
                    if (res.ok) {
                        currentIFlow = await res.json();
                        renderIFlowMetadata(currentIFlow);
                    }
                } catch (err) {
                    alert("Error fetching iFlow: " + err.message);
                } finally {
                    btnSampleiFlow.innerHTML = "<span>📦</span> Load Sample iFlow";
                }
            });
        });
    }

    // --- AI Test Suite Generation ---
    btnGenerateTests.addEventListener("click", async () => {
        if (!currentIFlow) {
            alert("Please load an iFlow package first in Step 1!");
            switchTab("tab-import");
            return;
        }

        btnGenerateTests.disabled = true;
        btnGenerateTests.innerHTML = "<span>✨</span> Synthesizing Test Cases...";

        try {
            const res = await fetch("/api/v1/testsuite/generate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ iflow_metadata: currentIFlow, num_cases_per_category: 1 })
            });

            if (res.ok) {
                const data = await res.json();
                currentTestCases = data.test_cases;
                renderTestCaseList(currentTestCases);
                renderMockRoutes(currentTestCases);
            }
        } catch (err) {
            alert("Error generating test suite: " + err.message);
        } finally {
            btnGenerateTests.disabled = false;
            btnGenerateTests.innerHTML = "<span>✨</span> Generate AI Test Cases";
        }
    });

    // --- Render Test Case List & Details ---
    function renderTestCaseList(cases) {
        document.getElementById("testCaseCount").innerText = `${cases.length} Cases`;
        const listEl = document.getElementById("testCaseList");

        listEl.innerHTML = cases.map((c, idx) => {
            const tagClass = c.category === "happy_path" ? "tag-happy" : (c.category === "boundary" ? "tag-boundary" : "tag-negative");
            return `
                <div class="test-item ${idx === 0 ? 'selected' : ''}" data-idx="${idx}">
                    <div class="test-item-title">[${c.id}] ${c.name}</div>
                    <div class="test-item-meta">
                        <span class="${tagClass}">${c.category.toUpperCase()}</span>
                        <span>Expected HTTP ${c.expected_status}</span>
                    </div>
                </div>
            `;
        }).join("");

        document.querySelectorAll(".test-item").forEach(item => {
            item.addEventListener("click", () => {
                document.querySelectorAll(".test-item").forEach(i => i.classList.remove("selected"));
                item.classList.add("selected");
                const idx = parseInt(item.getAttribute("data-idx"));
                selectTestCase(cases[idx]);
            });
        });

        if (cases.length) {
            selectTestCase(cases[0]);
        }
    }

    function selectTestCase(tc) {
        selectedTestCase = tc;
        document.getElementById("selectedTestName").innerText = `[${tc.id}] ${tc.name}`;
        document.getElementById("selectedTestCategory").innerText = tc.category.toUpperCase();

        document.getElementById("testDetailBody").innerHTML = `
            <div>
                <strong style="font-size: 0.8rem; color: var(--text-muted);">Description:</strong>
                <p style="font-size: 0.85rem; color: #fff; margin-top: 0.2rem;">${tc.description}</p>
            </div>
            <div>
                <strong style="font-size: 0.8rem; color: var(--text-muted);">Payload (${tc.payload_type}):</strong>
                <textarea class="code-editor mt-2" id="payloadEditor">${tc.payload}</textarea>
            </div>
        `;
    }

    // --- Render Mock Routes ---
    function renderMockRoutes(cases) {
        const routesList = document.getElementById("mockRoutesList");
        let allRules = [];
        cases.forEach(c => {
            if (c.mock_rules) allRules.push(...c.mock_rules);
        });

        if (!allRules.length) {
            routesList.innerHTML = `<p class="text-muted">No mock rules active.</p>`;
            return;
        }

        routesList.innerHTML = allRules.map(r => `
            <div style="padding: 0.6rem 0.8rem; background: rgba(255,255,255,0.03); border-radius: 6px; margin-bottom: 0.5rem; border: 1px solid var(--border-color);">
                <strong style="color: var(--accent-purple); font-size: 0.85rem;">/mock/${r.receiver_name}</strong>
                <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.2rem;">
                    Returns HTTP ${r.response_status}
                </div>
            </div>
        `).join("");
    }

    // --- Execute Test Suite ---
    btnRunSuite.addEventListener("click", async () => {
        if (!currentTestCases.length) {
            alert("No test cases to run! Generate AI test cases first.");
            switchTab("tab-test-studio");
            return;
        }

        btnRunSuite.disabled = true;
        btnRunSuite.innerHTML = "<span>⚡</span> Executing Suite...";

        try {
            const res = await fetch("/api/v1/testsuite/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    cpi_endpoint: "/mock/simulated_cpi_inbound",
                    test_cases: currentTestCases,
                    enable_mpl_check: true
                })
            });

            if (res.ok) {
                lastReport = await res.json();
                renderExecutionResults(lastReport);
                fetchInterceptedLogs();
            }
        } catch (err) {
            alert("Execution error: " + err.message);
        } finally {
            btnRunSuite.disabled = false;
            btnRunSuite.innerHTML = "<span>⚡</span> Execute Full Test Suite";
        }
    });

    function renderExecutionResults(report) {
        document.getElementById("statTotal").innerText = report.total_tests;
        document.getElementById("statPassed").innerText = report.passed;
        document.getElementById("statFailed").innerText = report.failed;
        document.getElementById("statDuration").innerText = `${report.duration_ms} ms`;

        btnExportJUnit.style.display = report.junit_xml ? "inline-flex" : "none";

        const tbody = document.getElementById("resultsTableBody");
        tbody.innerHTML = report.results.map(r => {
            const isPass = r.status === "PASS";
            const badgeClass = isPass ? "status-badge" : "badge";
            const badgeColor = isPass ? "color: var(--success);" : "color: var(--danger); border-color: rgba(239,68,68,0.3);";
            return `
                <tr>
                    <td><span class="badge ${badgeClass}" style="${badgeColor}">${r.status}</span></td>
                    <td><code>${r.test_id}</code></td>
                    <td><strong>${r.name}</strong></td>
                    <td><span class="badge">${r.category}</span></td>
                    <td>${r.execution_time_ms} ms</td>
                    <td><code>${r.cpi_mpl_id}</code></td>
                    <td><button class="btn btn-sm btn-secondary" onclick="alert('Raw Response:\\n' + ${JSON.stringify(r.actual_response)})">View Output</button></td>
                </tr>
            `;
        }).join("");
    }

    // --- Mock Refresh & Clear ---
    btnRefreshIntercepts.addEventListener("click", fetchInterceptedLogs);
    btnClearMocks.addEventListener("click", async () => {
        await fetch("/api/v1/mock/clear", { method: "POST" });
        fetchInterceptedLogs();
    });

    async function fetchInterceptedLogs() {
        try {
            const res = await fetch("/api/v1/mock/intercepts");
            if (res.ok) {
                const data = await res.json();
                const container = document.getElementById("interceptsLog");
                if (!data.intercepts.length) {
                    container.innerHTML = `<div class="empty-state"><p>No outbound calls intercepted yet.</p></div>`;
                    return;
                }

                container.innerHTML = data.intercepts.map(i => `
                    <div style="font-family: var(--font-mono); font-size: 0.75rem; border-bottom: 1px solid var(--border-color); padding: 0.5rem 0;">
                        <span style="color: var(--accent-cyan);">[${i.method}]</span> 
                        <strong style="color: #fff;">${i.receiver_name}</strong> (${i.path})
                        <pre style="margin-top: 0.3rem; color: var(--text-muted);">${i.body}</pre>
                    </div>
                `).join("");
            }
        } catch (err) {
            console.error("Fetch intercepts error", err);
        }
    }

    // Export JUnit XML
    btnExportJUnit.addEventListener("click", () => {
        if (!lastReport || !lastReport.junit_xml) return;
        const blob = new Blob([lastReport.junit_xml], { type: "text/xml" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "junit_cpi_report.xml";
        a.click();
    });
});
