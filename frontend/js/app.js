document.addEventListener("DOMContentLoaded", () => {
    // --- Global State ---
    let currentIFlow = None
    let currentTestCases = []
    let selectedTestCase = null
    let lastReport = null
    let connectedHostUrl = ""
    let parsedInboundPath = "/http/horizon"
    let isSimMode = false

    // --- DOM Elements ---
    const navTabs = document.querySelectorAll(".nav-tab")
    const tabContents = document.querySelectorAll(".tab-content")

    const btnSampleiFlow = document.getElementById("btnSampleiFlow")
    const btnConnectTenant = document.getElementById("btnConnectTenant")
    const tenantModal = document.getElementById("tenantModal")
    const serviceKeyJson = document.getElementById("serviceKeyJson")
    const btnSubmitTenant = document.getElementById("btnSubmitTenant")
    const iflowPickerModal = document.getElementById("iflowPickerModal")
    const iflowPickerList = document.getElementById("iflowPickerList")

    const dropZone = document.getElementById("dropZone")
    const fileInput = document.getElementById("fileInput")

    const btnGenerateTests = document.getElementById("btnGenerateTests")
    const btnRunSuite = document.getElementById("btnRunSuite")
    
    const toggleLive = document.getElementById("toggleLive")
    const toggleSim = document.getElementById("toggleSim")
    
    const targetCpiEndpoint = document.getElementById("targetCpiEndpoint")
    const runtimeAuthType = document.getElementById("runtimeAuthType")
    const itRtServiceKeyJson = document.getElementById("itRtServiceKeyJson")
    const btnToggleCredsBox = document.getElementById("btnToggleCredsBox")
    const credsExpandBox = document.getElementById("credsExpandBox")

    const btnClearMocks = document.getElementById("btnClearMocks")
    const btnRefreshIntercepts = document.getElementById("btnRefreshIntercepts")

    // --- Tab Navigation ---
    navTabs.forEach(tab => {
        tab.addEventListener("click", () => {
            const target = tab.getAttribute("data-tab")
            switchTab(target)
        })
    })

    function switchTab(tabId) {
        navTabs.forEach(t => {
            if (t.getAttribute("data-tab") === tabId) t.classList.add("active")
            else t.classList.remove("active")
        })
        tabContents.forEach(c => {
            if (c.id === tabId) c.classList.add("active")
            else c.classList.remove("active")
        })
    }

    // --- Live / Sim Toggle Switch ---
    if (toggleLive && toggleSim) {
        toggleLive.addEventListener("click", () => {
            isSimMode = false
            toggleLive.classList.add("active")
            toggleSim.classList.remove("active")
        })
        toggleSim.addEventListener("click", () => {
            isSimMode = true
            toggleSim.classList.add("active")
            toggleLive.classList.remove("active")
        })
    }

    // --- Collapsible Runtime Key Box ---
    if (btnToggleCredsBox && credsExpandBox) {
        btnToggleCredsBox.addEventListener("click", () => {
            credsExpandBox.style.display = credsExpandBox.style.display === "none" ? "block" : "none"
        })
    }

    // --- Helper to extract relative endpoint path ---
    function extractRelativePath(fullOrRelUrl) {
        if (!fullOrRelUrl) return "/http/horizon"
        let clean = fullOrRelUrl.trim()
        if (clean.startsWith("http://") || clean.startsWith("https://")) {
            try {
                const u = new URL(clean)
                clean = u.pathname
            } catch (e) {
                const parts = clean.split(".hana.ondemand.com")
                if (parts.length > 1) clean = parts[1]
            }
        }
        if (!clean.startsWith("/")) clean = "/" + clean
        return clean
    }

    // --- Extract Service Key Fields Helper ---
    function extractServiceKeyFields(keyObj) {
        if (!keyObj || typeof keyObj !== 'object') return {}
        let src = keyObj.oauth || keyObj.credentials || keyObj.service_key || keyObj
        let hostUrl = src.url || src.management_url || src.service_url || src.api || ''
        if (!hostUrl && src.endpoints) {
            hostUrl = src.endpoints.api || src.endpoints.url || src.endpoints.web || ''
        }
        let clientId = src.clientid || src.client_id || (src.oauth ? (src.oauth.clientid || src.oauth.client_id) : '')
        let clientSecret = src.clientsecret || src.client_secret || (src.oauth ? (src.oauth.clientsecret || src.oauth.client_secret) : '')
        let tokenUrl = src.tokenurl || src.token_url || (src.oauth ? (src.oauth.tokenurl || src.oauth.token_url) : '')
        return { hostUrl: hostUrl.trim(), clientId: clientId.trim(), clientSecret: clientSecret.trim(), tokenUrl: tokenUrl.trim() }
    }

    function updateTargetEndpointFromItRt() {
        if (!itRtServiceKeyJson || !itRtServiceKeyJson.value.trim()) return
        try {
            const parsed = JSON.parse(itRtServiceKeyJson.value.trim())
            const ext = extractServiceKeyFields(parsed)
            if (ext.hostUrl && targetCpiEndpoint) {
                const rtHost = ext.hostUrl.replace(/\/$/, '')
                const relPath = extractRelativePath(targetCpiEndpoint.value || parsedInboundPath)
                targetCpiEndpoint.value = `${rtHost}${relPath}`
            }
        } catch (e) {}
    }

    if (itRtServiceKeyJson) {
        itRtServiceKeyJson.addEventListener("input", updateTargetEndpointFromItRt)
    }

    // --- Load Sample iFlow ---
    btnSampleiFlow.addEventListener("click", async () => {
        btnSampleiFlow.disabled = true
        btnSampleiFlow.innerText = "Loading iFlow..."
        try {
            const res = await fetch("/api/v1/cpi/fetch-iflow/SalesOrder_S4HANA_Creation")
            if (res.ok) {
                currentIFlow = await res.json()
                await onIFlowLoaded(currentIFlow)
            }
        } catch (err) {
            alert("Error loading sample iFlow: " + err.message)
        } finally {
            btnSampleiFlow.disabled = false
            btnSampleiFlow.innerHTML = "<span>📦</span> Load Sample iFlow"
        }
    })

    // --- File Drag & Drop Upload ---
    dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); })
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"))
    dropZone.addEventListener("drop", (e) => {
        e.preventDefault()
        dropZone.classList.remove("dragover")
        if (e.dataTransfer.files.length) handleFileUpload(e.dataTransfer.files[0])
    })

    fileInput.addEventListener("change", (e) => {
        if (e.target.files.length) handleFileUpload(e.target.files[0])
    })

    async function handleFileUpload(file) {
        if (!file.name.endsWith(".zip")) { alert("Please upload an iFlow .zip bundle"); return }
        const formData = new FormData()
        formData.append("file", file)
        try {
            const res = await fetch("/api/v1/iflow/parse", { method: "POST", body: formData })
            if (res.ok) {
                currentIFlow = await res.json()
                await onIFlowLoaded(currentIFlow)
            } else {
                const err = await res.json()
                alert("Upload failed: " + (err.detail || err.error))
            }
        } catch (err) { alert("Upload error: " + err.message) }
    }

    // --- Process Loaded iFlow ---
    async function onIFlowLoaded(metadata) {
        renderIFlowMetadata(metadata)
        
        parsedInboundPath = (metadata.inbound_endpoint && metadata.inbound_endpoint.url_path) ? metadata.inbound_endpoint.url_path : "/http/horizon"
        const relPath = extractRelativePath(parsedInboundPath)

        if (itRtServiceKeyJson && itRtServiceKeyJson.value.trim()) {
            updateTargetEndpointFromItRt()
        } else if (targetCpiEndpoint) {
            const host = connectedHostUrl ? connectedHostUrl.replace(/\/$/, '') : "https://cpi-gtxsss73.it-cpitrial03-rt.cfapps.ap21.hana.ondemand.com"
            targetCpiEndpoint.value = `${host}${relPath}`
        }

        if (btnGenerateTests) await generateTestSuiteInternal(metadata)
        switchTab("tab-test-studio")
    }

    // --- Render iFlow Metadata ---
    function renderIFlowMetadata(metadata) {
        document.getElementById("iflowStatusBadge").className = "badge status-badge"
        document.getElementById("iflowStatusBadge").innerText = `Active: ${metadata.id || metadata.name}`

        const receiversHtml = metadata.receiver_endpoints.map(r => 
            `<span class="badge" style="background: rgba(99, 102, 241, 0.12); border-color: rgba(99, 102, 241, 0.25); color: #818cf8;">${r.name} (${r.adapter_type})</span>`
        ).join(" ")

        const scriptsHtml = metadata.groovy_scripts.map(s => `<code>${s}</code>`).join(", ") || "None"

        document.getElementById("metadataBody").innerHTML = `
            <div style="display: flex; flex-direction: column; gap: 0.85rem;">
                <div>
                    <h4 style="font-size: 1.05rem; color: #fff; font-weight: 600;">${metadata.name}</h4>
                    <p style="font-size: 0.78rem; color: var(--text-secondary); margin-top: 0.15rem;">${metadata.description || ''}</p>
                </div>
                <hr style="border: 0; border-top: 1px solid var(--border-color);" />
                <div>
                    <strong style="font-size: 0.78rem; color: var(--text-muted);">Inbound Sender Endpoint:</strong>
                    <div style="margin-top: 0.25rem;">
                        <code style="color: var(--accent-cyan); font-size: 0.82rem;">[${metadata.inbound_endpoint.adapter_type}] ${metadata.inbound_endpoint.url_path}</code>
                    </div>
                </div>
                <div>
                    <strong style="font-size: 0.78rem; color: var(--text-muted);">Receiver Systems to Mock:</strong>
                    <div style="margin-top: 0.35rem; display: flex; gap: 0.35rem; flex-wrap: wrap;">${receiversHtml}</div>
                </div>
                <div>
                    <strong style="font-size: 0.78rem; color: var(--text-muted);">Detected Groovy Scripts:</strong>
                    <div style="margin-top: 0.25rem; font-size: 0.78rem;">${scriptsHtml}</div>
                </div>
            </div>
        `
    }

    // --- SAP Tenant Modal Connection Handler ---
    btnConnectTenant.addEventListener("click", () => {
        tenantModal.classList.add("active")
        setTimeout(() => { if (serviceKeyJson) serviceKeyJson.focus(); }, 150)
    })

    btnSubmitTenant.addEventListener("click", async () => {
        const rawJson = serviceKeyJson ? serviceKeyJson.value.trim() : ""
        let tenantUrl = document.getElementById("cpiHost") ? document.getElementById("cpiHost").value.trim() : ""
        let clientId = document.getElementById("clientId") ? document.getElementById("clientId").value.trim() : ""
        let clientSecret = document.getElementById("clientSecret") ? document.getElementById("clientSecret").value.trim() : ""
        let tokenUrl = document.getElementById("tokenUrl") ? document.getElementById("tokenUrl").value.trim() : ""
        let iflowName = document.getElementById("iflowName") ? document.getElementById("iflowName").value.trim() : "Horizon"
        let version = document.getElementById("iflowVersion") ? document.getElementById("iflowVersion").value.trim() : "active"

        if (rawJson) {
            try {
                const parsed = JSON.parse(rawJson)
                const ext = extractServiceKeyFields(parsed)
                if (ext.hostUrl) tenantUrl = ext.hostUrl
                if (ext.clientId) clientId = ext.clientId
                if (ext.clientSecret) clientSecret = ext.clientSecret
                if (ext.tokenUrl) tokenUrl = ext.tokenUrl
            } catch (e) {}
        }

        connectedHostUrl = tenantUrl
        btnSubmitTenant.innerText = "Connecting to SAP Tenant..."
        btnSubmitTenant.disabled = true

        try {
            const res = await fetch("/api/v1/cpi/connect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tenant_url: tenantUrl, client_id: clientId, client_secret: clientSecret, token_url: tokenUrl, iflow_name: iflowName, version: version })
            })
            const data = await res.json()
            if (res.ok && data.status !== "ERROR") {
                tenantModal.classList.remove("active")
                showIFlowPickerModal(data.iflows || [], true)
            } else {
                alert(`❌ Connection Response:\n\n${data.error || 'Connection failed.'}`)
            }
        } catch (err) {
            alert("Network Error: " + err.message)
        } finally {
            btnSubmitTenant.innerText = "Fetch iFlow"
            btnSubmitTenant.disabled = false
        }
    })

    function showIFlowPickerModal(iflows, isLive) {
        if (!iflows.length) { alert("No iFlows found on tenant."); return }
        document.querySelector("#iflowPickerModal h3").innerText = isLive ? "🟢 Connected Live Tenant - Select iFlow" : "📦 Demo Mode - Select Sample iFlow"

        iflowPickerList.innerHTML = iflows.map(item => `
            <div class="test-item" style="margin-bottom: 0.5rem; display: flex; justify-content: space-between; align-items: center;" data-id="${item.id}">
                <div>
                    <strong style="color: #fff; font-size: 0.85rem;">${item.name}</strong>
                    <div style="font-size: 0.72rem; color: var(--text-muted);">ID: <code>${item.id}</code> | Version: ${item.version || 'active'}</div>
                </div>
                <button class="btn btn-sm btn-primary select-iflow-btn" data-id="${item.id}">Select iFlow & Generate Tests</button>
            </div>
        `).join("")

        iflowPickerModal.classList.add("active")

        document.querySelectorAll(".select-iflow-btn").forEach(btn => {
            btn.addEventListener("click", async () => {
                const iflowId = btn.getAttribute("data-id")
                iflowPickerModal.classList.remove("active")
                btnSampleiFlow.innerText = "Fetching iFlow..."
                try {
                    const res = await fetch(`/api/v1/cpi/fetch-iflow/${iflowId}`)
                    if (res.ok) {
                        currentIFlow = await res.json()
                        await onIFlowLoaded(currentIFlow)
                    }
                } catch (err) {
                    alert("Error fetching iFlow: " + err.message)
                } finally {
                    btnSampleiFlow.innerHTML = "<span>📦</span> Load Sample iFlow"
                }
            })
        })
    }

    // --- AI Test Suite Generation Logic ---
    btnGenerateTests.addEventListener("click", async () => {
        if (!currentIFlow) { alert("Please load an iFlow package first in Step 1!"); switchTab("tab-import"); return }
        await generateTestSuiteInternal(currentIFlow)
    })

    async function generateTestSuiteInternal(iflowMeta) {
        btnGenerateTests.disabled = true
        btnGenerateTests.innerHTML = "<span>✨</span> Synthesizing Test Cases..."
        try {
            const res = await fetch("/api/v1/testsuite/generate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ iflow_metadata: iflowMeta, num_cases_per_category: 1 })
            })
            if (res.ok) {
                const data = await res.json()
                currentTestCases = data.test_cases
                renderTestCaseList(currentTestCases)
                renderMockRoutes(currentTestCases)
            }
        } catch (err) {
            console.error("Error generating test suite:", err)
        } finally {
            btnGenerateTests.disabled = false
            btnGenerateTests.innerHTML = "<span>✨</span> Generate AI Tests"
        }
    }

    function renderTestCaseList(cases) {
        document.getElementById("testCaseCount").innerText = `${cases.length} Cases`
        const listEl = document.getElementById("testCaseList")

        listEl.innerHTML = cases.map((c, idx) => {
            const tagClass = c.category === "happy_path" ? "tag-happy" : (c.category === "boundary" ? "tag-boundary" : "tag-negative")
            const resultBadge = c.last_result ? 
                `<span class="badge" style="${c.last_result.status === 'PASS' ? 'color: var(--success); background: rgba(16,185,129,0.1); border-color: rgba(16,185,129,0.2);' : 'color: var(--danger); background: rgba(239,68,68,0.1); border-color: rgba(239,68,68,0.2);'}">${c.last_result.status} (${c.last_result.status_code}) ${c.last_result.execution_time_ms}ms</span>` 
                : `<span class="badge" style="font-size: 0.68rem;">Ready</span>`

            return `
                <div class="test-item ${idx === 0 ? 'selected' : ''}" data-idx="${idx}" id="test-item-${c.id}">
                    <div class="test-item-header">
                        <div class="test-item-title">[${c.id}] ${c.name}</div>
                    </div>
                    <div class="test-item-meta">
                        <span class="${tagClass}">${c.category.toUpperCase()}</span>
                        <span>Expected HTTP ${c.expected_status}</span>
                    </div>
                    <div class="test-item-footer">
                        <div id="test-result-badge-${c.id}">${resultBadge}</div>
                        <button class="btn btn-run-single" onclick="window.runSingleTestFromList('${c.id}', event)">
                            ▶ Run Test
                        </button>
                    </div>
                </div>
            `
        }).join("")

        document.querySelectorAll(".test-item").forEach(item => {
            item.addEventListener("click", (e) => {
                if (e.target.tagName === "BUTTON") return
                document.querySelectorAll(".test-item").forEach(i => i.classList.remove("selected"))
                item.classList.add("selected")
                const idx = parseInt(item.getAttribute("data-idx"))
                if (cases[idx]) selectTestCase(cases[idx])
            })
        })

        if (cases.length) selectTestCase(cases[0])
    }

    window.runSingleTestFromList = async function(testId, event) {
        if (event) event.stopPropagation()
        const tc = currentTestCases.find(c => c.id === testId)
        if (!tc) return

        const tcItem = document.getElementById(`test-item-${testId}`)
        const badgeEl = document.getElementById(`test-result-badge-${testId}`)
        if (tcItem) {
            document.querySelectorAll(".test-item").forEach(i => i.classList.remove("selected"))
            tcItem.classList.add("selected")
            selectTestCase(tc)
        }

        if (badgeEl) badgeEl.innerHTML = `<span class="badge" style="color: var(--accent-cyan);">Running...</span>`

        const report = await executeTestSuiteInternal([tc])
        if (report && report.results && report.results.length) {
            const res = report.results[0]
            tc.last_result = res
            const isPass = res.status === "PASS"
            if (badgeEl) {
                badgeEl.innerHTML = `<span class="badge" style="${isPass ? 'color: var(--success); background: rgba(16,185,129,0.1); border-color: rgba(16,185,129,0.25);' : 'color: var(--danger); background: rgba(239,68,68,0.1); border-color: rgba(239,68,68,0.25);'}">${res.status} HTTP ${res.status_code} (${res.execution_time_ms}ms)</span>`
            }
            selectTestCase(tc)
            fetchInterceptedLogs()
        }
    }

    function selectTestCase(tc) {
        selectedTestCase = tc
        document.getElementById("selectedTestName").innerText = `[${tc.id}] ${tc.name}`
        document.getElementById("selectedTestCategory").innerText = tc.category.toUpperCase()

        const editorVal = document.getElementById("payloadEditor") ? document.getElementById("payloadEditor").value : tc.payload
        if (selectedTestCase) selectedTestCase.payload = editorVal

        let resultSection = ""
        if (tc.last_result) {
            const r = tc.last_result
            const isPass = r.status === "PASS"
            const statusColor = isPass ? "color: var(--success);" : "color: var(--danger);"
            resultSection = `
                <div style="margin-top: 1rem; padding: 0.85rem; background: var(--bg-inset); border: 1px solid var(--border-color); border-radius: var(--radius-sm);">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                        <strong style="font-size: 0.82rem; ${statusColor}">Latest Live Run Result: ${r.status} (HTTP ${r.status_code})</strong>
                        <span style="font-size: 0.74rem; color: var(--text-muted);">${r.execution_time_ms} ms</span>
                    </div>
                    <div style="font-size: 0.78rem; font-family: var(--font-mono); color: var(--accent-cyan); margin-bottom: 0.4rem;">
                        SAP MPL ID: ${r.cpi_mpl_id || 'N/A'}
                    </div>
                    <div style="font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 0.4rem;">Response Body:</div>
                    <pre style="background: rgba(0,0,0,0.4); padding: 0.6rem; border-radius: 4px; font-size: 0.76rem; max-height: 140px; overflow-y: auto; color: #fff;">${r.actual_response || '(Empty Response)'}</pre>
                </div>
            `
        }

        document.getElementById("testDetailBody").innerHTML = `
            <div>
                <strong style="font-size: 0.78rem; color: var(--text-muted);">Description:</strong>
                <p style="font-size: 0.82rem; color: #fff; margin-top: 0.15rem;">${tc.description}</p>
            </div>
            <div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.25rem;">
                    <strong style="font-size: 0.78rem; color: var(--text-muted);">Payload (${tc.payload_type}):</strong>
                    <button class="btn btn-sm btn-run-single" onclick="window.runSingleTestFromList('${tc.id}', event)">▶ Run Test Case</button>
                </div>
                <textarea class="code-editor" id="payloadEditor">${tc.payload}</textarea>
            </div>
            ${resultSection}
        `

        document.getElementById("payloadEditor").addEventListener("input", (e) => {
            tc.payload = e.target.value
        })
    }

    function renderMockRoutes(cases) {
        const routesList = document.getElementById("mockRoutesList")
        let allRules = []
        cases.forEach(c => { if (c.mock_rules) allRules.push(...c.mock_rules); })
        if (!allRules.length) { routesList.innerHTML = `<p class="text-muted" style="font-size: 0.82rem;">No mock rules active.</p>`; return }

        routesList.innerHTML = allRules.map(r => `
            <div style="padding: 0.55rem 0.75rem; background: rgba(255,255,255,0.02); border-radius: 6px; margin-bottom: 0.45rem; border: 1px solid var(--border-color);">
                <strong style="color: #818cf8; font-size: 0.82rem;">/mock/${r.receiver_name}</strong>
                <div style="font-size: 0.74rem; color: var(--text-muted); margin-top: 0.15rem;">Returns HTTP ${r.response_status}</div>
            </div>
        `).join("")
    }

    async function executeTestSuiteInternal(testCasesToRun) {
        const endpointValue = targetCpiEndpoint ? targetCpiEndpoint.value.trim() : ""
        const finalEndpoint = isSimMode ? "/mock/simulated_cpi_inbound" : (endpointValue || "https://cpi-gtxsss73.it-cpitrial03-rt.cfapps.ap21.hana.ondemand.com/http/horizon")

        let reqPayload = {
            cpi_endpoint: finalEndpoint,
            test_cases: testCasesToRun,
            enable_mpl_check: true,
            runtime_auth_type: runtimeAuthType ? runtimeAuthType.value : "oauth2"
        }

        const rtRaw = itRtServiceKeyJson ? itRtServiceKeyJson.value.trim() : ""
        if (rtRaw) {
            try {
                const parsed = JSON.parse(rtRaw)
                const ext = extractServiceKeyFields(parsed)
                reqPayload.credentials = {
                    client_id: ext.clientId,
                    client_secret: ext.clientSecret,
                    token_url: ext.tokenUrl,
                    tenant_url: ext.hostUrl
                }
                if (ext.hostUrl && targetCpiEndpoint) {
                    const rtHost = ext.hostUrl.replace(/\/$/, '')
                    const relPath = extractRelativePath(targetCpiEndpoint.value || parsedInboundPath)
                    targetCpiEndpoint.value = `${rtHost}${relPath}`
                    if (!isSimMode) {
                        reqPayload.cpi_endpoint = targetCpiEndpoint.value
                    }
                }
            } catch (e) {
                alert("Invalid JSON in Runtime Service Key (it-rt) box.")
                return null
            }
        }

        try {
            const res = await fetch("/api/v1/testsuite/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(reqPayload)
            })

            if (res.ok) {
                const report = await res.json()
                return report
            }
        } catch (err) {
            alert("Execution error: " + err.message)
        }
        return null
    }

    // --- Execute Full Test Suite ---
    btnRunSuite.addEventListener("click", async () => {
        if (!currentTestCases.length) {
            alert("No test cases to run! Generate AI test cases first.")
            switchTab("tab-test-studio")
            return
        }

        btnRunSuite.disabled = true
        btnRunSuite.innerHTML = `<span>⚡</span> Executing Suite on ${isSimMode ? 'Simulation' : 'Live CPI'}...`

        try {
            const report = await executeTestSuiteInternal(currentTestCases)
            if (report && report.results) {
                lastReport = report
                report.results.forEach(r => {
                    const matchedTc = currentTestCases.find(c => c.id === r.test_id)
                    if (matchedTc) matchedTc.last_result = r
                })
                renderTestCaseList(currentTestCases)
                fetchInterceptedLogs()
            }
        } catch (err) {
            alert("Execution error: " + err.message)
        } finally {
            btnRunSuite.disabled = false
            btnRunSuite.innerHTML = "<span>⚡</span> Execute Full Suite"
        }
    })

    btnRefreshIntercepts.addEventListener("click", fetchInterceptedLogs)
    btnClearMocks.addEventListener("click", async () => {
        await fetch("/api/v1/mock/clear", { method: "POST" })
        fetchInterceptedLogs()
    })

    async function fetchInterceptedLogs() {
        try {
            const res = await fetch("/api/v1/mock/intercepts")
            if (res.ok) {
                const data = await res.json()
                const container = document.getElementById("interceptsLog")
                if (!data.intercepts.length) {
                    container.innerHTML = `<div class="empty-state"><p>No outbound calls intercepted yet.</p></div>`
                    return
                }
                container.innerHTML = data.intercepts.map(i => `
                    <div style="font-family: var(--font-mono); font-size: 0.75rem; border-bottom: 1px solid var(--border-color); padding: 0.5rem 0;">
                        <span style="color: var(--accent-cyan);">[${i.method}]</span> 
                        <strong style="color: #fff;">${i.receiver_name}</strong> (${i.path})
                        <pre style="margin-top: 0.3rem; color: var(--text-muted);">${i.body}</pre>
                    </div>
                `).join("")
            }
        } catch (err) {}
    }
})
