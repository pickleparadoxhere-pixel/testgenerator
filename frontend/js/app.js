// SAP CPI IFlow Test Payload Generator & Live Runner Engine
document.addEventListener("DOMContentLoaded", () => {
  // Global State
  let currentIFlowId = "";
  let currentAnalysis = null;
  let currentMetadata = null;
  let activeTenantUrl = "";
  let generatedPayloads = [];

  // DOM Elements - Modal & Connection
  const btnOpenTenantModal = document.getElementById("btnOpenTenantModal");
  const btnCloseTenantModal = document.getElementById("btnCloseTenantModal");
  const tenantModal = document.getElementById("tenantModal");
  const btnConnectTenant = document.getElementById("btnConnectTenant");
  const btnLoadModalJson = document.getElementById("btnLoadModalJson");
  const modalApiServiceKeyJson = document.getElementById("modalApiServiceKeyJson");
  const modalRuntimeServiceKeyJson = document.getElementById("modalRuntimeServiceKeyJson");
  const modalTenantUrl = document.getElementById("modalTenantUrl");
  const modalAuthType = document.getElementById("modalAuthType");
  const modalTokenUrl = document.getElementById("modalTokenUrl");
  const modalClientId = document.getElementById("modalClientId");
  const modalClientSecret = document.getElementById("modalClientSecret");
  const modalIflowName = document.getElementById("modalIflowName");
  const modalConnectStatus = document.getElementById("modalConnectStatus");
  const tenantStatusBadge = document.getElementById("tenantStatusBadge");

  // DOM Elements - Artifacts & Upload
  const liveArtifactsContainer = document.getElementById("liveArtifactsContainer");
  const liveArtifactsTbody = document.getElementById("liveArtifactsTbody");
  const liveArtifactCount = document.getElementById("liveArtifactCount");
  const dropZone = document.getElementById("dropZone");
  const fileInput = document.getElementById("fileInput");
  const dirInput = document.getElementById("dirInput");
  const dropFilename = document.getElementById("dropFilename");
  const btnAnalyze = document.getElementById("btnAnalyze");
  const analyzeStatus = document.getElementById("analyzeStatus");

  // DOM Elements - Analysis & Payloads
  const sectionResults = document.getElementById("section-results");
  const payloadCardsList = document.getElementById("payloadCardsList");
  const reportMarkdownContainer = document.getElementById("reportMarkdownContainer");
  const btnDownloadReport = document.getElementById("btnDownloadReport");

  // DOM Elements - Test Window
  const sectionTestWindow = document.getElementById("section-test-window");
  const runtimeServiceKeyJson = document.getElementById("runtimeServiceKeyJson");
  const btnLoadRuntimeJson = document.getElementById("btnLoadRuntimeJson");
  const runtimeJsonStatus = document.getElementById("runtimeJsonStatus");
  const runtimeEndpoint = document.getElementById("runtimeEndpoint");
  const runtimeEndpointSource = document.getElementById("runtimeEndpointSource");
  const runtimeAuthType = document.getElementById("runtimeAuthType");
  const runtimeTokenUrl = document.getElementById("runtimeTokenUrl");
  const runtimePrincipal = document.getElementById("runtimePrincipal");
  const runtimeSecret = document.getElementById("runtimeSecret");
  const runtimePayloadSelect = document.getElementById("runtimePayloadSelect");
  const runtimeHeaders = document.getElementById("runtimeHeaders");
  const runtimeBody = document.getElementById("runtimeBody");
  const btnSendRuntimeTest = document.getElementById("btnSendRuntimeTest");
  const runtimeTestStatus = document.getElementById("runtimeTestStatus");
  const runtimeResponseCard = document.getElementById("runtimeResponseCard");
  const resStatusBadge = document.getElementById("resStatusBadge");
  const resLatency = document.getElementById("resLatency");
  const resMplIdBox = document.getElementById("resMplIdBox");
  const resMplId = document.getElementById("resMplId");
  const resHeadersText = document.getElementById("resHeadersText");
  const resBodyText = document.getElementById("resBodyText");

  // DOM Elements - Mock Server
  const mockUrl = document.getElementById("mockUrl");
  const mockReceiverName = document.getElementById("mockReceiverName");
  const mockStatus = document.getElementById("mockStatus");
  const mockContentType = document.getElementById("mockContentType");
  const mockBody = document.getElementById("mockBody");
  const btnSaveMock = document.getElementById("btnSaveMock");
  const mockSaveStatus = document.getElementById("mockSaveStatus");
  const btnLoadSample = document.getElementById("btnLoadSample");

  let selectedFiles = [];

  // --- Helper: Service Key JSON Parser ---
  function parseServiceKeyJson(str) {
    if (!str || !str.trim()) return null;
    try {
      const parsed = JSON.parse(str);
      const oauth = parsed.oauth || parsed.credentials || parsed.service_key || parsed;
      const url = oauth.url || oauth.management_url || oauth.service_url || oauth.api || parsed.tenant_url || parsed.url || "";
      const tokenUrl = oauth.tokenurl || oauth.token_url || parsed.token_url || parsed.tokenurl || "";
      const clientId = oauth.clientid || oauth.client_id || parsed.client_id || parsed.clientid || parsed.username || "";
      const clientSecret = oauth.clientsecret || oauth.client_secret || parsed.client_secret || parsed.clientsecret || parsed.password || "";
      const iflowId = parsed.iflowId || parsed.artifactId || parsed.iflow_name || "";
      const version = parsed.version || parsed.iflowVersion || "active";

      return { url: url.trim(), tokenUrl: tokenUrl.trim(), clientId: clientId.trim(), clientSecret: clientSecret.trim(), iflowId: iflowId.trim(), version: version.trim() };
    } catch (e) {
      return null;
    }
  }

  // Auto-fill Modal JSON (handles both api and it-rt keys dynamically)
  if (btnLoadModalJson) {
    btnLoadModalJson.addEventListener("click", () => {
      let apiRes = parseServiceKeyJson(modalApiServiceKeyJson ? modalApiServiceKeyJson.value : "");
      let rtRes = parseServiceKeyJson(modalRuntimeServiceKeyJson ? modalRuntimeServiceKeyJson.value : "");

      if (apiRes) {
        if (apiRes.url) modalTenantUrl.value = apiRes.url;
        if (apiRes.tokenUrl) modalTokenUrl.value = apiRes.tokenUrl;
        if (apiRes.clientId) modalClientId.value = apiRes.clientId;
        if (apiRes.clientSecret) modalClientSecret.value = apiRes.clientSecret;
        if (apiRes.iflowId && modalIflowName) modalIflowName.value = apiRes.iflowId;
        modalAuthType.value = apiRes.tokenUrl ? "oauth" : "basic";
      }

      if (rtRes) {
        if (runtimeServiceKeyJson) runtimeServiceKeyJson.value = modalRuntimeServiceKeyJson.value;
        if (rtRes.tokenUrl) runtimeTokenUrl.value = rtRes.tokenUrl;
        if (rtRes.clientId) runtimePrincipal.value = rtRes.clientId;
        if (rtRes.clientSecret) runtimeSecret.value = rtRes.clientSecret;
        runtimeAuthType.value = rtRes.tokenUrl ? "oauth" : "basic";

        if (!modalTenantUrl.value && rtRes.url) {
          modalTenantUrl.value = rtRes.url.replace("-rt.cfapps", ".cfapps");
        }
      }

      if (modalConnectStatus) {
        modalConnectStatus.className = "status-msg success";
        modalConnectStatus.textContent = "Service keys populated!";
      }
    });
  }

  // Auto-fill Runtime JSON
  if (btnLoadRuntimeJson && runtimeServiceKeyJson) {
    btnLoadRuntimeJson.addEventListener("click", () => {
      const res = parseServiceKeyJson(runtimeServiceKeyJson.value);
      if (res) {
        if (res.tokenUrl) runtimeTokenUrl.value = res.tokenUrl;
        if (res.clientId) runtimePrincipal.value = res.clientId;
        if (res.clientSecret) runtimeSecret.value = res.clientSecret;
        runtimeAuthType.value = res.tokenUrl ? "oauth" : "basic";

        if (res.url) {
          let rtHost = res.url.replace(".it-cpitrial03.", ".it-cpitrial03-rt.").replace(/\/$/, "");
          let adapterPath = (currentMetadata && currentMetadata.inbound_endpoint && currentMetadata.inbound_endpoint.url_path) ? currentMetadata.inbound_endpoint.url_path : "";
          
          if (adapterPath.startsWith("http://") || adapterPath.startsWith("https://")) {
            try {
              let urlObj = new URL(adapterPath);
              adapterPath = urlObj.pathname;
            } catch(e) {}
          }
          if (!adapterPath) {
            adapterPath = currentIFlowId ? `/http/${currentIFlowId.toLowerCase()}` : "/http/sender";
          }
          if (!adapterPath.startsWith("/")) {
            adapterPath = "/" + adapterPath;
          }
          runtimeEndpoint.value = `${rtHost}${adapterPath}`;
        }
        if (runtimeJsonStatus) {
          runtimeJsonStatus.className = "status-msg success";
          runtimeJsonStatus.textContent = "Runtime credentials & endpoint auto-populated!";
        }
      } else {
        if (runtimeJsonStatus) {
          runtimeJsonStatus.className = "status-msg error";
          runtimeJsonStatus.textContent = "Invalid Service Key JSON format.";
        }
      }
    });
  }

  // Modal Handlers
  if (btnOpenTenantModal && tenantModal) {
    btnOpenTenantModal.addEventListener("click", () => {
      tenantModal.style.display = "flex";
    });
  }

  if (btnCloseTenantModal && tenantModal) {
    btnCloseTenantModal.addEventListener("click", () => {
      tenantModal.style.display = "none";
    });
  }

  // Connect Tenant API
  if (btnConnectTenant) {
    btnConnectTenant.addEventListener("click", async () => {
      modalConnectStatus.className = "status-msg";
      modalConnectStatus.textContent = "Authenticating with BTP OAuth...";
      btnConnectTenant.disabled = true;

      const payload = {
        tenant_url: modalTenantUrl.value.trim(),
        auth_type: modalAuthType.value,
        token_url: modalTokenUrl.value.trim(),
        client_id: modalClientId.value.trim(),
        client_secret: modalClientSecret.value.trim(),
        iflow_name: (modalIflowName ? modalIflowName.value.trim() : "")
      };

      try {
        const resp = await fetch("/api/v1/cpi/connect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await resp.json();

        if (resp.ok && data.status === "LIVE_SUCCESS") {
          modalConnectStatus.className = "status-msg success";
          modalConnectStatus.textContent = data.message;
          activeTenantUrl = payload.tenant_url;

          tenantStatusBadge.className = "status-pill status-connected";
          tenantStatusBadge.innerHTML = `<span class="dot"></span> Connected: ${new URL(payload.tenant_url).hostname}`;

          renderArtifactsTable(data.iflows || []);
          setTimeout(() => { tenantModal.style.display = "none"; }, 1200);
        } else {
          modalConnectStatus.className = "status-msg error";
          modalConnectStatus.textContent = data.error || "Connection failed.";
        }
      } catch (err) {
        modalConnectStatus.className = "status-msg error";
        modalConnectStatus.textContent = `Network error: ${err.message}`;
      } finally {
        btnConnectTenant.disabled = false;
      }
    });
  }

  // Render Artifacts Table
  function renderArtifactsTable(artifacts) {
    if (!artifacts || artifacts.length === 0) return;
    liveArtifactsContainer.style.display = "block";
    liveArtifactCount.textContent = `${artifacts.length} found`;
    liveArtifactsTbody.innerHTML = "";

    artifacts.forEach((art, idx) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input type="radio" name="selected_artifact" value="${art.id}" ${idx === 0 ? "checked" : ""}></td>
        <td><strong>${art.id}</strong></td>
        <td>${art.name || art.id}</td>
        <td><span class="badge">${art.version || "active"}</span></td>
        <td>${art.package_id || "DefaultPackage"}</td>
        <td>
          <button class="btn btn-secondary btn-sm btn-fetch-art" data-id="${art.id}">
            <span>⚡</span> Analyze & Payloads
          </button>
        </td>
      `;
      liveArtifactsTbody.appendChild(tr);
    });

    document.querySelectorAll(".btn-fetch-art").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const id = e.currentTarget.getAttribute("data-id");
        fetchAndAnalyzeIFlow(id);
      });
    });
  }

  // Fetch & Analyze iFlow
  async function fetchAndAnalyzeIFlow(iflowId) {
    currentIFlowId = iflowId;
    analyzeStatus.className = "status-msg";
    analyzeStatus.textContent = `Downloading & analyzing '${iflowId}'...`;

    try {
      const resp = await fetch(`/api/v1/cpi/fetch-iflow/${iflowId}`);
      const data = await resp.json();

      if (resp.ok) {
        analyzeStatus.className = "status-msg success";
        analyzeStatus.textContent = `Analysis complete for '${iflowId}'!`;
        displayAnalysisResults(data);
      } else {
        analyzeStatus.className = "status-msg error";
        analyzeStatus.textContent = data.error || "Failed to analyze iFlow.";
      }
    } catch (err) {
      analyzeStatus.className = "status-msg error";
      analyzeStatus.textContent = `Error: ${err.message}`;
    }
  }

  // Display Analysis Results & Payloads
  function displayAnalysisResults(data) {
    currentMetadata = data.metadata || {};
    currentAnalysis = data.analysis || {};
    generatedPayloads = currentAnalysis.payloads || [];

    // Show Results & Test Window Sections
    sectionResults.style.display = "block";
    sectionTestWindow.style.display = "block";

    // 1. Render Test Payload Cards
    renderPayloadCards(generatedPayloads);

    // 2. Render Markdown Report
    const markdownStr = currentAnalysis.report_markdown || "No report generated.";
    reportMarkdownContainer.textContent = markdownStr;

    // 3. Auto-populate Deployed Endpoint URL based strictly on sender adapter url_path
    let adapterPath = (currentMetadata.inbound_endpoint && currentMetadata.inbound_endpoint.url_path) ? currentMetadata.inbound_endpoint.url_path : "";
    let rtCreds = parseServiceKeyJson(runtimeServiceKeyJson ? runtimeServiceKeyJson.value : "");
    let rtHost = (rtCreds && rtCreds.url) ? rtCreds.url.replace(".it-cpitrial03.", ".it-cpitrial03-rt.").replace(/\/$/, "") : (activeTenantUrl ? activeTenantUrl.replace(".it-cpitrial03.", ".it-cpitrial03-rt.").replace(/\/$/, "") : "");

    if (adapterPath) {
      if (adapterPath.startsWith("http://") || adapterPath.startsWith("https://")) {
        runtimeEndpoint.value = adapterPath;
      } else {
        if (!adapterPath.startsWith("/")) adapterPath = "/" + adapterPath;
        runtimeEndpoint.value = rtHost ? `${rtHost}${adapterPath}` : adapterPath;
      }
    } else {
      let fallbackPath = currentIFlowId ? `/http/${currentIFlowId.toLowerCase()}` : "/http/sender";
      runtimeEndpoint.value = rtHost ? `${rtHost}${fallbackPath}` : `https://cpi-tenant-rt.cfapps.sap.com${fallbackPath}`;
    }

    // 4. Populate Payload Select dropdown
    runtimePayloadSelect.innerHTML = "";
    if (generatedPayloads.length > 0) {
      generatedPayloads.forEach((p, idx) => {
        const opt = document.createElement("option");
        opt.value = idx;
        opt.textContent = `${p.scenario} (${p.format})`;
        runtimePayloadSelect.appendChild(opt);
      });
      runtimeBody.value = generatedPayloads[0].body;
    }

    sectionResults.scrollIntoView({ behavior: "smooth" });
  }

  // Render Payload Cards
  function renderPayloadCards(payloads) {
    payloadCardsList.innerHTML = "";
    if (!payloads || payloads.length === 0) {
      payloadCardsList.innerHTML = `<p class="hint">No payload schemas detected in this iFlow.</p>`;
      return;
    }

    payloads.forEach((p, idx) => {
      const card = document.createElement("div");
      card.className = "payload-card";
      card.innerHTML = `
        <div class="payload-card-header">
          <h3>Scenario: ${p.scenario}</h3>
          <span class="badge">${p.format.toUpperCase()}</span>
        </div>
        <p class="hint">Derived from <code>${p.source}</code></p>
        <pre class="code-block">${escapeHtml(p.body)}</pre>
        <div class="payload-card-actions">
          <button class="btn btn-secondary btn-sm btn-copy-payload" data-index="${idx}">📋 Copy Payload</button>
          <button class="btn btn-primary btn-sm btn-load-runner" data-index="${idx}">⚡ Load into Test Runner</button>
        </div>
      `;
      payloadCardsList.appendChild(card);
    });

    document.querySelectorAll(".btn-copy-payload").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const idx = e.currentTarget.getAttribute("data-index");
        const body = payloads[idx].body;
        navigator.clipboard.writeText(body);
        e.currentTarget.textContent = "✓ Copied!";
        setTimeout(() => { e.currentTarget.textContent = "📋 Copy Payload"; }, 1500);
      });
    });

    document.querySelectorAll(".btn-load-runner").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const idx = e.currentTarget.getAttribute("data-index");
        runtimePayloadSelect.value = idx;
        runtimeBody.value = payloads[idx].body;
        sectionTestWindow.scrollIntoView({ behavior: "smooth" });
      });
    });
  }

  if (btnDownloadReport) {
    btnDownloadReport.addEventListener("click", () => {
      if (!currentAnalysis || !currentAnalysis.report_markdown) return;
      const blob = new Blob([currentAnalysis.report_markdown], { type: "text/markdown" });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const filename = (currentMetadata && currentMetadata.id) ? `Analysis_Report_${currentMetadata.id}.md` : "Analysis_Report.md";
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    });
  }

  // Payload Selector change event
  if (runtimePayloadSelect) {
    runtimePayloadSelect.addEventListener("change", (e) => {
      const idx = e.target.value;
      if (generatedPayloads[idx]) {
        runtimeBody.value = generatedPayloads[idx].body;
      }
    });
  }

  // Tab Switching logic
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

      e.currentTarget.classList.add("active");
      const targetId = e.currentTarget.getAttribute("data-tab");
      document.getElementById(targetId).classList.add("active");
    });
  });

  // Call Deployed IFlow
  if (btnSendRuntimeTest) {
    btnSendRuntimeTest.addEventListener("click", async () => {
      runtimeTestStatus.className = "status-msg";
      runtimeTestStatus.textContent = "Sending live request to SAP CPI...";
      btnSendRuntimeTest.disabled = true;
      runtimeResponseCard.style.display = "none";

      const payload = {
        endpoint: runtimeEndpoint.value.trim(),
        principal: runtimePrincipal.value.trim(),
        secret: runtimeSecret.value.trim(),
        auth_type: runtimeAuthType.value,
        token_url: runtimeTokenUrl.value.trim(),
        headers: runtimeHeaders.value.trim(),
        body: runtimeBody.value
      };

      try {
        const resp = await fetch("/api/v1/runtime/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await resp.json();

        if (resp.ok || data.status) {
          runtimeTestStatus.className = "status-msg success";
          runtimeTestStatus.textContent = "Execution finished.";
          displayRuntimeResponse(data);
        } else {
          runtimeTestStatus.className = "status-msg error";
          runtimeTestStatus.textContent = data.error || "Invocation error.";
        }
      } catch (err) {
        runtimeTestStatus.className = "status-msg error";
        runtimeTestStatus.textContent = `Network error: ${err.message}`;
      } finally {
        btnSendRuntimeTest.disabled = false;
      }
    });
  }

  // Display Runtime Execution Response
  function displayRuntimeResponse(data) {
    runtimeResponseCard.style.display = "block";
    const status = data.status || 200;
    const isSuccess = status >= 200 && status < 300;

    resStatusBadge.className = isSuccess ? "badge badge-success" : "badge badge-error";
    resStatusBadge.textContent = `HTTP ${status} ${data.reason || ""}`;
    resLatency.textContent = `⏱️ ${data.elapsed_ms || 0} ms`;

    if (data.mpl_id) {
      resMplIdBox.style.display = "inline-flex";
      resMplId.textContent = data.mpl_id;
    } else {
      resMplIdBox.style.display = "none";
    }

    resHeadersText.textContent = JSON.stringify(data.headers || {}, null, 2);
    resBodyText.textContent = data.body || "(Empty response body)";

    runtimeResponseCard.scrollIntoView({ behavior: "smooth" });
  }

  // Save Receiver Mock Config
  if (btnSaveMock) {
    btnSaveMock.addEventListener("click", async () => {
      mockSaveStatus.className = "status-msg";
      mockSaveStatus.textContent = "Saving mock response rule...";
      btnSaveMock.disabled = true;

      const payload = {
        receiver_name: mockReceiverName.value.trim() || "receiver",
        status: parseInt(mockStatus.value, 10) || 200,
        content_type: mockContentType.value.trim() || "application/xml",
        body: mockBody.value
      };

      try {
        const resp = await fetch("/api/v1/mock/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await resp.json();

        if (resp.ok) {
          mockSaveStatus.className = "status-msg success";
          mockSaveStatus.textContent = `Saved! Mock Receiver URL: ${data.url || ''}`;
          if (data.url) mockUrl.value = data.url;
        } else {
          mockSaveStatus.className = "status-msg error";
          mockSaveStatus.textContent = data.error || "Failed to save mock.";
        }
      } catch (err) {
        mockSaveStatus.className = "status-msg error";
        mockSaveStatus.textContent = `Error: ${err.message}`;
      } finally {
        btnSaveMock.disabled = false;
      }
    });
  }

  // Load Sample iFlow
  if (btnLoadSample) {
    btnLoadSample.addEventListener("click", () => {
      fetchAndAnalyzeIFlow("Horizon");
    });
  }

  // --- CPI Discovery Agent Engine ---
  const discoveryQueryInput = document.getElementById("discoveryQueryInput");
  const geminiApiKeyInput = document.getElementById("geminiApiKeyInput");
  const btnAskDiscovery = document.getElementById("btnAskDiscovery");
  const discoveryStatus = document.getElementById("discoveryStatus");
  const discoveryResultsBox = document.getElementById("discoveryResultsBox");
  const agentAnswerText = document.getElementById("agentAnswerText");
  const discoveryTbody = document.getElementById("discoveryTbody");

  // Load saved Gemini API Key
  if (geminiApiKeyInput) {
    const savedKey = localStorage.getItem("gemini_api_key") || "";
    geminiApiKeyInput.value = savedKey;
    geminiApiKeyInput.addEventListener("change", () => {
      localStorage.setItem("gemini_api_key", geminiApiKeyInput.value.trim());
    });
  }

  // Quick Prompt Chips
  document.querySelectorAll(".chip-btn").forEach(chip => {
    chip.addEventListener("click", (e) => {
      const q = e.currentTarget.getAttribute("data-query");
      if (discoveryQueryInput) discoveryQueryInput.value = q;
      executeDiscoveryQuery(q);
    });
  });

  if (btnAskDiscovery) {
    btnAskDiscovery.addEventListener("click", () => {
      const q = discoveryQueryInput ? discoveryQueryInput.value.trim() : "";
      if (q) executeDiscoveryQuery(q);
    });
  }

  if (discoveryQueryInput) {
    discoveryQueryInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const q = discoveryQueryInput.value.trim();
        if (q) executeDiscoveryQuery(q);
      }
    });
  }

  async function executeDiscoveryQuery(queryText) {
    if (!queryText) return;
    if (discoveryStatus) {
      discoveryStatus.className = "status-msg";
      discoveryStatus.textContent = "Querying CPI Discovery Agent...";
    }
    if (btnAskDiscovery) btnAskDiscovery.disabled = true;

    const apiKey = geminiApiKeyInput ? geminiApiKeyInput.value.trim() : "";
    if (apiKey) localStorage.setItem("gemini_api_key", apiKey);

    try {
      const resp = await fetch("/api/v1/cpi/discovery/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: queryText,
          gemini_api_key: apiKey
        })
      });
      const data = await resp.json();

      if (resp.ok) {
        if (discoveryStatus) {
          discoveryStatus.className = "status-msg success";
          if (data.query_type === "ARTIFACTS_LIST") {
            discoveryStatus.textContent = `Found ${data.matched_count} artifact(s).`;
          } else {
            discoveryStatus.textContent = `Query processed cleanly.`;
          }
        }
        displayDiscoveryResults(data);
      } else {
        if (discoveryStatus) {
          discoveryStatus.className = "status-msg error";
          discoveryStatus.textContent = data.error || "Query execution failed.";
        }
      }
    } catch (err) {
      if (discoveryStatus) {
        discoveryStatus.className = "status-msg error";
        discoveryStatus.textContent = `Error: ${err.message}`;
      }
    } finally {
      if (btnAskDiscovery) btnAskDiscovery.disabled = false;
    }
  }

  function displayDiscoveryResults(data) {
    if (!discoveryResultsBox) return;
    discoveryResultsBox.style.display = "block";

    const discoveryThead = document.getElementById("discoveryThead");
    const discoveryTableContainer = discoveryThead ? discoveryThead.closest(".table-responsive") : null;
    const queryType = data.query_type || "TEXT_ANSWER";

    // Format markdown in answer text
    let formattedAnswer = escapeHtml(data.answer || "")
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/### (.*?)\n/g, "<h3 style='margin-top: 12px; margin-bottom: 6px; color: var(--primary);'>$1</h3>")
      .replace(/• (.*?)\n/g, "• $1<br>")
      .replace(/\n\n/g, "<br><br>");
    agentAnswerText.innerHTML = formattedAnswer;

    // Hide iFlow Table COMPLETELY unless query_type is ARTIFACTS_LIST!
    if (queryType !== "ARTIFACTS_LIST" || !data.table_data || data.table_data.length === 0) {
      if (discoveryTableContainer) discoveryTableContainer.style.display = "none";
      discoveryTbody.innerHTML = "";
      return;
    }

    // Explicit ARTIFACTS_LIST presentation
    if (discoveryTableContainer) discoveryTableContainer.style.display = "block";
    if (discoveryThead) {
      discoveryThead.style.display = "table-header-group";
      discoveryThead.innerHTML = `
        <tr>
          <th>Artifact ID</th>
          <th>Name</th>
          <th>Version</th>
          <th>Package</th>
          <th>Status</th>
          <th>Adapters / Tech</th>
          <th>Action</th>
        </tr>
      `;
    }

    discoveryTbody.innerHTML = "";
    data.table_data.forEach(item => {
      const tr = document.createElement("tr");
      const adaptersStr = (item.adapters || []).map(ad => `<span class="badge">${escapeHtml(ad)}</span>`).join(" ");
      const isDeployed = item.status === "DEPLOYED" || item.is_deployed;

      tr.innerHTML = `
        <td><strong>${escapeHtml(item.id || "")}</strong></td>
        <td>${escapeHtml(item.name || item.id || "")}</td>
        <td><span class="badge">${escapeHtml(item.version || "1.0.0")}</span></td>
        <td>${escapeHtml(item.package_id || item.package_name || "DefaultPackage")}</td>
        <td><span class="badge ${isDeployed ? 'badge-success' : ''}">${item.runtime_status || item.status || 'DESIGNTIME'}</span></td>
        <td>${adaptersStr || '<span class="badge">HTTPS</span>'}</td>
        <td>
          <button class="btn btn-secondary btn-sm btn-disc-analyze" data-id="${escapeHtml(item.id || "")}">
            <span>⚡</span> Analyze & Payloads
          </button>
        </td>
      `;
      discoveryTbody.appendChild(tr);
    });

    document.querySelectorAll(".btn-disc-analyze").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const id = e.currentTarget.getAttribute("data-id");
        fetchAndAnalyzeIFlow(id);
      });
    });

    discoveryResultsBox.scrollIntoView({ behavior: "smooth" });
  }

  // File Upload Handlers
  if (fileInput) {
    fileInput.addEventListener("change", (e) => {
      if (e.target.files.length > 0) {
        selectedFiles = Array.from(e.target.files);
        dropFilename.textContent = selectedFiles.map(f => f.name).join(", ");
        btnAnalyze.disabled = false;
      }
    });
  }

  if (btnAnalyze) {
    btnAnalyze.addEventListener("click", async () => {
      if (selectedFiles.length === 0) return;
      analyzeStatus.className = "status-msg";
      analyzeStatus.textContent = "Uploading & analyzing package...";
      btnAnalyze.disabled = true;

      const formData = new FormData();
      selectedFiles.forEach(f => formData.append("files", f));

      try {
        const resp = await fetch("/api/v1/iflow/parse", {
          method: "POST",
          body: formData
        });
        const data = await resp.json();
        if (resp.ok) {
          analyzeStatus.className = "status-msg success";
          analyzeStatus.textContent = "Analysis complete!";
          displayAnalysisResults(data);
        } else {
          analyzeStatus.className = "status-msg error";
          analyzeStatus.textContent = data.error || "Failed to parse file.";
        }
      } catch (err) {
        analyzeStatus.className = "status-msg error";
        analyzeStatus.textContent = `Error: ${err.message}`;
      } finally {
        btnAnalyze.disabled = false;
      }
    });
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
});
