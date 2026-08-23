from __future__ import annotations

import argparse
import json
import re
import tempfile
import zipfile
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from .analyzer import Analysis, AnalysisError, IFlowAnalyzer, local_name
from .demo import create_demo_archives
from .sap import RuntimeHttpClient, SapCpiClient, SapCpiError


MAX_UPLOAD_BYTES = 100 * 1024 * 1024

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SAP CPI IFlow Test Payload Generator</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #08111f; color: #e9f0fb; }
    main { width: min(1080px, calc(100% - 32px)); margin: 0 auto; padding: 64px 0; }
    .eyebrow { color: #63d7c6; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; font-size: 12px; }
    h1 { font-size: clamp(36px, 6vw, 68px); line-height: 1; max-width: 850px; margin: 16px 0; letter-spacing: -.04em; }
    .intro { color: #aebed3; font-size: 18px; line-height: 1.6; max-width: 750px; }
    .panel { margin-top: 36px; padding: 28px; border: 1px solid #253651; border-radius: 18px; background: #0d192a; box-shadow: 0 24px 80px #0006; }
    .drop { display: grid; place-items: center; min-height: 220px; padding: 28px; border: 1px dashed #49617f; border-radius: 14px; background: #101e31; text-align: center; transition: .2s; }
    .drop.drag { border-color: #63d7c6; background: #102b32; }
    input[type=file] { display: none; }
    button, .choose { border: 0; border-radius: 10px; padding: 12px 18px; background: #63d7c6; color: #07141a; font: inherit; font-weight: 750; cursor: pointer; }
    input, select { width: 100%; border: 1px solid #334965; border-radius: 9px; padding: 11px 12px; background: #07101d; color: #e9f0fb; font: inherit; }
    textarea { width: 100%; min-height: 180px; resize: vertical; border: 1px solid #334965; border-radius: 9px; padding: 12px; background: #07101d; color: #e9f0fb; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
    label.field { display: grid; gap: 7px; color: #aebed3; font-size: 14px; }
    .fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    .wide { grid-column: 1 / -1; }
    .artifact-table { width: 100%; border-collapse: collapse; margin-top: 18px; }
    .artifact-table th, .artifact-table td { padding: 10px; border-bottom: 1px solid #253651; text-align: left; }
    .artifact-table input[type=checkbox] { display: block; width: auto; }
    @media (max-width: 700px) { .fields { grid-template-columns: 1fr; } }
    button:disabled { opacity: .45; cursor: wait; }
    .filename { margin: 16px 0; color: #cad7e8; }
    .hint, #status { color: #8296b1; font-size: 14px; }
    .actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 20px; }
    a.choose { text-decoration: none; display: inline-block; }
    #result { display: none; margin-top: 28px; }
    #payloads { display: none; margin-top: 28px; }
    .payload-card { margin-top: 16px; padding: 18px; border: 1px solid #253651; border-radius: 14px; background: #0a1524; }
    .payload-card h3 { margin: 0 0 6px; }
    .payload-card p { color: #8296b1; margin: 0 0 12px; }
    #test-window, #mock-window, #runtime-response { display: none; margin-top: 28px; }
    .warning { color: #f6c56f; font-size: 14px; line-height: 1.5; }
    pre { max-height: 65vh; overflow: auto; padding: 22px; border: 1px solid #253651; border-radius: 14px; background: #07101d; color: #dbe8f7; white-space: pre-wrap; line-height: 1.5; }
    .error { color: #ff9c9c !important; }
  </style>
</head>
<body>
<main>
  <div class="eyebrow">Local static analysis</div>
  <h1>Turn an SAP CPI IFlow into test payloads.</h1>
  <p class="intro">Upload a design-time ZIP. The analyzer inventories the package, reconstructs the flow, detects required headers and properties, and generates schema-derived XML or JSON—all on this machine.</p>
  <section class="panel">
    <h2>Connect to SAP CPI</h2>
    <p class="hint">Credentials stay in this page and are sent only to this local process for the current API request. They are never saved or logged.</p>
    <label class="field wide">Paste SAP connection JSON
      <textarea id="sap-json" placeholder='{"url":"https://tenant.example.com","clientid":"...","clientsecret":"...","tokenurl":"https://.../oauth/token","iflowId":"...","version":"..."}' spellcheck="false"></textarea>
    </label>
    <div class="actions"><button id="sap-load-json">Fill fields from JSON</button><span id="sap-json-status" class="hint"></span></div>
    <div class="fields">
      <label class="field wide">Tenant API URL<input id="sap-url" type="url" placeholder="https://your-tenant.example.com" autocomplete="off"></label>
      <label class="field">Authentication<select id="sap-auth"><option value="basic">Basic username/password</option><option value="oauth">OAuth client credentials</option></select></label>
      <label class="field oauth-field" hidden>OAuth token URL<input id="sap-token-url" type="url" placeholder="https://.../oauth/token" autocomplete="off"></label>
      <label class="field"><span id="sap-principal-label">Username</span><input id="sap-principal" autocomplete="username"></label>
      <label class="field"><span id="sap-secret-label">Password</span><input id="sap-secret" type="password" autocomplete="current-password"></label>
    </div>
    <h3>Direct IFlow fetch</h3>
    <div class="fields">
      <label class="field">Technical Artifact ID<input id="sap-direct-id" value="test" autocomplete="off"></label>
      <label class="field">Version<input id="sap-direct-version" value="1.0.0" autocomplete="off"></label>
    </div>
    <div class="actions"><button id="sap-direct-analyze">Fetch this IFlow and analyze</button><span id="sap-status" class="hint"></span></div>
  </section>
  <section class="panel">
    <h2>Or analyze local exports</h2>
    <div id="drop" class="drop">
      <div>
        <label class="choose" for="file">Choose ZIPs / files</label>
        <label class="choose" for="directory">Choose folder</label>
        <input id="file" type="file" multiple>
        <input id="directory" type="file" webkitdirectory multiple>
        <div id="filename" class="filename">or drop multiple files here</div>
        <div class="hint">Mix ZIPs and unzipped IFlow artifacts. Maximum request size: 100 MB. Nothing is uploaded remotely.</div>
      </div>
    </div>
    <div class="actions">
      <button id="analyze" disabled>Analyze IFlow</button>
      <button id="demo">Run synthetic Req/Res demo</button>
      <span id="status"></span>
    </div>
    <div class="actions">
      <a class="choose" href="/demo/request.zip">Download demo Req ZIP</a>
      <a class="choose" href="/demo/response.zip">Download demo Res ZIP</a>
    </div>
    <div id="result">
      <div id="payloads"><h2>Generated XML test payloads</h2><div id="payload-list"></div></div>
      <div id="test-window">
        <h2>Test generated payload against deployed IFlow</h2>
        <p class="hint">Use a separate Process Integration Runtime <code>integration-flow</code> service key. Credentials exist only for this call.</p>
        <label class="field wide">Paste integration-flow service-key JSON<textarea id="runtime-json" spellcheck="false" placeholder='{"oauth":{"url":"https://runtime.example.com","clientid":"...","clientsecret":"...","tokenurl":"https://.../oauth/token"}}'></textarea></label>
        <div class="actions"><button id="runtime-load-json">Fill runtime fields</button><span id="runtime-json-status" class="hint"></span></div>
        <div class="fields">
          <label class="field wide">Deployed IFlow endpoint URL<input id="runtime-endpoint" type="url" placeholder="Derived from the runtime key and fetched sender adapter"><span id="runtime-endpoint-source" class="hint">Analyze an IFlow to discover its configured sender endpoint.</span></label>
          <label class="field">Authentication<select id="runtime-auth"><option value="oauth">OAuth client credentials</option><option value="basic">Basic username/password</option></select></label>
          <label class="field oauth-runtime-field">OAuth token URL<input id="runtime-token-url" type="url"></label>
          <label class="field"><span id="runtime-principal-label">Client ID</span><input id="runtime-principal" autocomplete="username"></label>
          <label class="field"><span id="runtime-secret-label">Client secret</span><input id="runtime-secret" type="password" autocomplete="current-password"></label>
          <label class="field wide">Generated XML payload<select id="runtime-payload-select"></select></label>
          <label class="field wide">Request headers (JSON)<textarea id="runtime-headers" spellcheck="false">{"Content-Type":"application/xml"}</textarea></label>
          <label class="field wide">Request XML<textarea id="runtime-body" spellcheck="false"></textarea></label>
        </div>
        <div class="actions"><button id="runtime-send">Call deployed IFlow</button><span id="runtime-status" class="hint"></span></div>
        <div id="runtime-response"><h3>Response</h3><div id="runtime-response-meta" class="hint"></div><pre id="runtime-response-headers"></pre><pre id="runtime-response-body"></pre></div>
        <div id="mock-window">
          <h3>Request-Reply mock target</h3>
          <p class="warning">This mock runs on localhost. SAP CPI can use it only if the tenant can reach this machine through your approved network route or tunnel, and the receiver address is configured to this URL.</p>
          <label class="field wide">Mock receiver URL<input id="mock-url" readonly></label>
          <div class="fields">
            <label class="field">Response status<input id="mock-status" type="number" min="200" max="599" value="200"></label>
            <label class="field">Content-Type<input id="mock-content-type" value="application/xml"></label>
            <label class="field wide">Mock response body<textarea id="mock-body" spellcheck="false">&lt;MockResponse&gt;&lt;Status&gt;SUCCESS&lt;/Status&gt;&lt;Message&gt;Synthetic receiver response&lt;/Message&gt;&lt;/MockResponse&gt;</textarea></label>
          </div>
          <div class="actions"><button id="mock-save">Save mock response</button><span id="mock-status-text" class="hint"></span></div>
        </div>
      </div>
      <div class="actions"><button id="download">Download analysis Markdown</button></div>
      <h2>Analysis report</h2>
      <pre id="report"></pre>
    </div>
  </section>
</main>
<script>
  const fileInput = document.querySelector('#file');
  const directoryInput = document.querySelector('#directory');
  const drop = document.querySelector('#drop');
  const analyze = document.querySelector('#analyze');
  const filename = document.querySelector('#filename');
  const status = document.querySelector('#status');
  const result = document.querySelector('#result');
  const report = document.querySelector('#report');
  let selected = [];
  let markdown = '';
  let generatedPayloads = [];

  function sapCredentials() {
    return {
      tenant_url: document.querySelector('#sap-url').value.trim(),
      auth_type: document.querySelector('#sap-auth').value,
      principal: document.querySelector('#sap-principal').value,
      secret: document.querySelector('#sap-secret').value,
      token_url: document.querySelector('#sap-token-url').value.trim()
    };
  }

  function jsonEntries(value, path = []) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    return Object.entries(value).flatMap(([key, child]) => {
      const entry = {key: key.toLowerCase().replace(/[^a-z0-9]/g, ''), value: child, path: [...path, key.toLowerCase()]};
      return [entry, ...jsonEntries(child, entry.path)];
    });
  }

  function pickJson(entries, keys, predicate = () => true) {
    for (const key of keys) {
      const match = entries.find(entry => entry.key === key && typeof entry.value === 'string' && entry.value.trim() && predicate(entry));
      if (match) return match.value.trim();
    }
    return '';
  }

  document.querySelector('#sap-load-json').addEventListener('click', () => {
    const jsonStatus = document.querySelector('#sap-json-status');
    try {
      const parsed = JSON.parse(document.querySelector('#sap-json').value);
      const entries = jsonEntries(parsed);
      const topLevelUrl = parsed && typeof parsed.url === 'string' ? parsed.url.trim() : '';
      const tenantUrl = pickJson(entries, ['apiurl', 'tenanturl', 'serviceurl', 'baseurl']) || topLevelUrl || pickJson(entries, ['url']);
      const tokenUrl = pickJson(entries, ['tokenurl', 'oauthurl', 'tokenendpoint']);
      const clientId = pickJson(entries, ['clientid', 'clientidentifier']);
      const username = pickJson(entries, ['username', 'user']);
      const clientSecret = pickJson(entries, ['clientsecret']);
      const password = pickJson(entries, ['password']);
      const artifactId = pickJson(entries, ['artifactid', 'iflowid', 'integrationflowid', 'flowid']);
      const version = pickJson(entries, ['artifactversion', 'iflowversion', 'flowversion', 'version']);
      const oauth = Boolean(clientId || clientSecret || tokenUrl);
      if (tenantUrl) document.querySelector('#sap-url').value = tenantUrl;
      document.querySelector('#sap-auth').value = oauth ? 'oauth' : 'basic';
      document.querySelector('#sap-auth').dispatchEvent(new Event('change'));
      if (tokenUrl) document.querySelector('#sap-token-url').value = tokenUrl;
      if (clientId || username) document.querySelector('#sap-principal').value = clientId || username;
      if (clientSecret || password) document.querySelector('#sap-secret').value = clientSecret || password;
      if (artifactId) document.querySelector('#sap-direct-id').value = artifactId;
      if (version) document.querySelector('#sap-direct-version').value = version;
      const populated = [tenantUrl, clientId || username, clientSecret || password, tokenUrl, artifactId, version].filter(Boolean).length;
      if (!populated) throw new Error('No recognized SAP connection fields were found');
      jsonStatus.className = 'hint'; jsonStatus.textContent = `${populated} fields populated. Review them before connecting.`;
    } catch (error) { jsonStatus.className = 'error'; jsonStatus.textContent = `Invalid or unsupported JSON: ${error.message}`; }
  });

  document.querySelector('#sap-auth').addEventListener('change', event => {
    const oauth = event.target.value === 'oauth';
    document.querySelector('.oauth-field').hidden = !oauth;
    document.querySelector('#sap-principal-label').textContent = oauth ? 'Client ID' : 'Username';
    document.querySelector('#sap-secret-label').textContent = oauth ? 'Client secret' : 'Password';
  });

  document.querySelector('#sap-direct-analyze').addEventListener('click', async event => {
    const artifactId = document.querySelector('#sap-direct-id').value.trim();
    const version = document.querySelector('#sap-direct-version').value.trim();
    const sapStatus = document.querySelector('#sap-status');
    if (!artifactId || !version) { sapStatus.textContent = 'Artifact ID and version are required'; return; }
    const button = event.currentTarget; button.disabled = true; sapStatus.className = 'hint'; sapStatus.textContent = `Fetching ${artifactId} (${version}) from SAP CPI…`; result.style.display = 'none';
    try {
      const artifact = {id: artifactId, name: artifactId, version, role: 'Auto'};
      const response = await fetch('/sap/analyze', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...sapCredentials(), artifacts: [artifact]})});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'SAP CPI analysis failed');
      renderResult(data); sapStatus.textContent = `${generatedPayloads.length} XML payload${generatedPayloads.length === 1 ? '' : 's'} generated from SAP CPI`;
    } catch (error) { sapStatus.className = 'error'; sapStatus.textContent = error.message; }
    finally { button.disabled = false; }
  });

  function renderResult(data) {
    markdown = data.report;
    generatedPayloads = data.payloads || [];
    report.textContent = markdown;
    const payloads = document.querySelector('#payloads');
    const list = document.querySelector('#payload-list');
    list.replaceChildren();
    generatedPayloads.forEach(payload => {
      const card = document.createElement('section'); card.className = 'payload-card';
      const title = document.createElement('h3'); title.textContent = payload.filename;
      const detail = document.createElement('p'); detail.textContent = `${payload.role} · ${payload.scenario}`;
      const download = document.createElement('button'); download.textContent = 'Download XML';
      download.addEventListener('click', () => downloadText(payload.body, payload.filename, 'application/xml'));
      const preview = document.createElement('pre'); preview.textContent = payload.body;
      card.append(title, detail, download, preview); list.append(card);
    });
    payloads.style.display = generatedPayloads.length ? 'block' : 'none';
    const testWindow = document.querySelector('#test-window');
    const payloadSelect = document.querySelector('#runtime-payload-select');
    payloadSelect.replaceChildren();
    generatedPayloads.forEach((payload, index) => {
      const option = document.createElement('option'); option.value = index; option.textContent = `${payload.filename} — ${payload.scenario}`; payloadSelect.append(option);
    });
    document.querySelector('#runtime-body').value = generatedPayloads[0]?.body || '';
    window.analysisTest = data.test || {request_reply: false, sender_paths: [], sender_endpoints: []};
    document.querySelector('#runtime-endpoint').value = '';
    const discoveredEndpoint = window.analysisTest.sender_endpoints?.[0];
    document.querySelector('#runtime-endpoint-source').textContent = discoveredEndpoint
      ? `Fetched from ${discoveredEndpoint.adapter || 'sender'} adapter configuration: ${discoveredEndpoint.configured_address} → ${discoveredEndpoint.runtime_path}`
      : 'No static sender endpoint was found in the fetched IFlow.';
    testWindow.style.display = generatedPayloads.length ? 'block' : 'none';
    document.querySelector('#mock-window').style.display = window.analysisTest.request_reply ? 'block' : 'none';
    document.querySelector('#mock-url').value = `${location.origin}/mock/receiver`;
    document.querySelector('#runtime-response').style.display = 'none';
    result.style.display = 'block';
  }

  document.querySelector('#runtime-payload-select').addEventListener('change', event => {
    document.querySelector('#runtime-body').value = generatedPayloads[Number(event.target.value)]?.body || '';
  });

  document.querySelector('#runtime-auth').addEventListener('change', event => {
    const oauth = event.target.value === 'oauth';
    document.querySelector('.oauth-runtime-field').hidden = !oauth;
    document.querySelector('#runtime-principal-label').textContent = oauth ? 'Client ID' : 'Username';
    document.querySelector('#runtime-secret-label').textContent = oauth ? 'Client secret' : 'Password';
  });

  document.querySelector('#runtime-load-json').addEventListener('click', () => {
    const runtimeStatus = document.querySelector('#runtime-json-status');
    try {
      const parsed = JSON.parse(document.querySelector('#runtime-json').value);
      const entries = jsonEntries(parsed);
      const baseUrl = (parsed && typeof parsed.url === 'string' ? parsed.url.trim() : '') || pickJson(entries, ['url', 'runtimeurl', 'serviceurl']);
      const tokenUrl = pickJson(entries, ['tokenurl', 'oauthurl', 'tokenendpoint']);
      const clientId = pickJson(entries, ['clientid', 'clientidentifier']);
      const username = pickJson(entries, ['username', 'user']);
      const clientSecret = pickJson(entries, ['clientsecret']);
      const password = pickJson(entries, ['password']);
      const oauth = Boolean(clientId || clientSecret || tokenUrl);
      document.querySelector('#runtime-auth').value = oauth ? 'oauth' : 'basic';
      document.querySelector('#runtime-auth').dispatchEvent(new Event('change'));
      if (tokenUrl) document.querySelector('#runtime-token-url').value = tokenUrl;
      if (clientId || username) document.querySelector('#runtime-principal').value = clientId || username;
      if (clientSecret || password) document.querySelector('#runtime-secret').value = clientSecret || password;
      if (baseUrl) {
        const senderPath = window.analysisTest?.sender_endpoints?.[0]?.runtime_path || window.analysisTest?.sender_paths?.[0] || '';
        document.querySelector('#runtime-endpoint').value = senderPath ? new URL(senderPath, `${baseUrl.replace(/[/]$/, '')}/`).href : baseUrl;
      }
      const populated = [baseUrl, tokenUrl, clientId || username, clientSecret || password].filter(Boolean).length;
      if (!populated) throw new Error('No recognized runtime service-key fields were found');
      runtimeStatus.className = 'hint'; runtimeStatus.textContent = `${populated} runtime fields populated. Review the endpoint before calling.`;
    } catch (error) { runtimeStatus.className = 'error'; runtimeStatus.textContent = `Invalid or unsupported JSON: ${error.message}`; }
  });

  document.querySelector('#runtime-send').addEventListener('click', async event => {
    const runtimeStatus = document.querySelector('#runtime-status'); const button = event.currentTarget;
    button.disabled = true; runtimeStatus.className = 'hint'; runtimeStatus.textContent = 'Calling deployed IFlow…'; document.querySelector('#runtime-response').style.display = 'none';
    try {
      const headers = JSON.parse(document.querySelector('#runtime-headers').value || '{}');
      const request = {
        endpoint: document.querySelector('#runtime-endpoint').value.trim(),
        auth_type: document.querySelector('#runtime-auth').value,
        token_url: document.querySelector('#runtime-token-url').value.trim(),
        principal: document.querySelector('#runtime-principal').value,
        secret: document.querySelector('#runtime-secret').value,
        headers,
        body: document.querySelector('#runtime-body').value
      };
      const response = await fetch('/runtime/test', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(request)});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Runtime call failed');
      document.querySelector('#runtime-response-meta').textContent = `HTTP ${data.status} ${data.reason || ''} · ${data.elapsed_ms} ms`;
      document.querySelector('#runtime-response-headers').textContent = JSON.stringify(data.headers, null, 2);
      document.querySelector('#runtime-response-body').textContent = data.body || '(empty response body)';
      document.querySelector('#runtime-response').style.display = 'block'; runtimeStatus.textContent = 'Response received';
    } catch (error) { runtimeStatus.className = 'error'; runtimeStatus.textContent = error.message; }
    finally { button.disabled = false; }
  });

  document.querySelector('#mock-save').addEventListener('click', async event => {
    const mockStatusText = document.querySelector('#mock-status-text'); const button = event.currentTarget; button.disabled = true;
    try {
      const response = await fetch('/mock/configure', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        status: Number(document.querySelector('#mock-status').value),
        content_type: document.querySelector('#mock-content-type').value,
        body: document.querySelector('#mock-body').value
      })});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not configure mock');
      mockStatusText.className = 'hint'; mockStatusText.textContent = `Mock ready at ${data.url}`;
    } catch (error) { mockStatusText.className = 'error'; mockStatusText.textContent = error.message; }
    finally { button.disabled = false; }
  });

  function downloadText(content, name, type) {
    const url = URL.createObjectURL(new Blob([content], {type}));
    const link = Object.assign(document.createElement('a'), {href: url, download: name});
    link.click(); URL.revokeObjectURL(url);
  }

  function select(files) {
    selected = Array.from(files || []);
    const bytes = selected.reduce((sum, file) => sum + file.size, 0);
    filename.textContent = selected.length ? `${selected.length} file${selected.length === 1 ? '' : 's'} · ${(bytes / 1024 / 1024).toFixed(2)} MB` : 'or drop multiple files here';
    analyze.disabled = selected.length === 0;
  }
  fileInput.addEventListener('change', () => select(fileInput.files));
  directoryInput.addEventListener('change', () => select(directoryInput.files));
  for (const event of ['dragenter', 'dragover']) drop.addEventListener(event, e => { e.preventDefault(); drop.classList.add('drag'); });
  for (const event of ['dragleave', 'drop']) drop.addEventListener(event, e => { e.preventDefault(); drop.classList.remove('drag'); });
  drop.addEventListener('drop', e => select(e.dataTransfer.files));
  analyze.addEventListener('click', async () => {
    if (!selected.length) return;
    analyze.disabled = true; status.className = ''; status.textContent = 'Analyzing…'; result.style.display = 'none';
    try {
      const form = new FormData();
      selected.forEach(file => form.append('files', file, file.webkitRelativePath || file.name));
      const response = await fetch('/analyze', { method: 'POST', body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Analysis failed');
      renderResult(data); status.textContent = generatedPayloads.length ? `${generatedPayloads.length} XML payload${generatedPayloads.length === 1 ? '' : 's'} generated` : 'Analysis complete; no XSD-derived XML payload found';
    } catch (error) { status.className = 'error'; status.textContent = error.message; }
    finally { analyze.disabled = false; }
  });
  document.querySelector('#demo').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true; status.className = ''; status.textContent = 'Generating and analyzing synthetic pair…'; result.style.display = 'none';
    try {
      const response = await fetch('/demo/analyze', {method: 'POST'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Demo analysis failed');
      renderResult(data); status.textContent = `${generatedPayloads.length} synthetic XML payloads generated`;
    } catch (error) { status.className = 'error'; status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.querySelector('#download').addEventListener('click', () => {
    downloadText(markdown, 'iflow-analysis-report.md', 'text/markdown');
  });
</script>
</body>
</html>
"""


class IFlowWebHandler(BaseHTTPRequestHandler):
    server_version = "IFlowTestPayload/0.1"
    mock_config: dict[str, object] = {
        "status": 200,
        "content_type": "application/xml",
        "body": "<MockResponse><Status>SUCCESS</Status><Message>Synthetic receiver response</Message></MockResponse>",
    }

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(HTTPStatus.OK, INDEX_HTML.encode(), "text/html; charset=utf-8")
        elif path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
        elif path == "/mock/receiver":
            self._send_mock_response()
        elif path in {"/demo/request.zip", "/demo/response.zip"}:
            with tempfile.TemporaryDirectory(prefix="iflow-demo-download-") as directory:
                archives = create_demo_archives(Path(directory))
                archive = archives[0] if path.endswith("request.zip") else archives[1]
                self._send(HTTPStatus.OK, archive.read_bytes(), "application/zip", archive.name)
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/runtime/test":
            self._handle_runtime_test()
            return
        if parsed.path == "/mock/configure":
            self._handle_mock_config()
            return
        if parsed.path == "/mock/receiver":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > MAX_UPLOAD_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Mock request exceeds 100 MB"})
                return
            if length:
                self.rfile.read(length)
            self._send_mock_response()
            return
        if parsed.path in {"/sap/artifacts", "/sap/analyze"}:
            self._handle_sap(parsed.path)
            return
        if parsed.path == "/demo/analyze":
            try:
                with tempfile.TemporaryDirectory(prefix="iflow-demo-") as directory:
                    archives = create_demo_archives(Path(directory))
                    analyses = [(path.name, self._infer_role(path.name), IFlowAnalyzer(path).analyze()) for path in archives]
                    report = self._paired_report(analyses)
                    self._json(HTTPStatus.OK, self._analysis_response(analyses, report))
            except (AnalysisError, OSError) as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path != "/analyze":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "ZIP must be between 1 byte and 100 MB"})
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Expected one or more uploaded files"})
            return
        data = self.rfile.read(length)
        try:
            with tempfile.TemporaryDirectory(prefix="iflow-web-") as directory:
                bundle = Path(directory) / "bundle"
                bundle.mkdir()
                uploads = self._multipart_files(content_type, data)
                if not uploads:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "No files were uploaded"})
                    return
                packages = self._assemble_bundle(bundle, uploads)
                analyses = [
                    (filename, self._infer_role(filename), IFlowAnalyzer(path).analyze())
                    for filename, path in packages
                ]
                report = analyses[0][2].to_markdown() if len(analyses) == 1 else self._paired_report(analyses)
            self._json(HTTPStatus.OK, self._analysis_response(analyses, report))
        except AnalysisError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except OSError:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Could not process the uploaded package"})

    def _handle_sap(self, path: str) -> None:
        try:
            payload = self._read_json_request()
            client = SapCpiClient(
                tenant_url=str(payload.get("tenant_url", "")),
                principal=str(payload.get("principal", "")),
                secret=str(payload.get("secret", "")),
                auth_type=str(payload.get("auth_type", "basic")),
                token_url=str(payload.get("token_url", "")),
            )
            if path == "/sap/artifacts":
                self._json(HTTPStatus.OK, {
                    "artifacts": [artifact.to_dict() for artifact in client.list_artifacts()],
                    "api_root": client.service_root,
                })
                return
            selections = payload.get("artifacts")
            if not isinstance(selections, list) or not selections or len(selections) > 20:
                raise SapCpiError("Select between 1 and 20 IFlow artifacts")
            with tempfile.TemporaryDirectory(prefix="iflow-sap-") as directory:
                analyses: list[tuple[str, str, Analysis]] = []
                for index, selection in enumerate(selections, start=1):
                    if not isinstance(selection, dict):
                        raise SapCpiError("Invalid artifact selection")
                    artifact_id = str(selection.get("id", ""))
                    version = str(selection.get("version", "active"))
                    display_name = str(selection.get("name") or artifact_id)
                    content = client.download_artifact(artifact_id, version)
                    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", artifact_id).strip("._") or f"artifact-{index}"
                    archive = Path(directory) / f"{safe_name}.zip"
                    archive.write_bytes(content)
                    chosen_role = str(selection.get("role", "Auto"))
                    role = self._infer_role(f"{display_name} {artifact_id}") if chosen_role == "Auto" else chosen_role
                    analyses.append((f"{display_name} [{artifact_id}]", role, IFlowAnalyzer(archive).analyze()))
                report = analyses[0][2].to_markdown() if len(analyses) == 1 else self._paired_report(analyses)
                self._json(HTTPStatus.OK, self._analysis_response(analyses, report))
        except (SapCpiError, AnalysisError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except (OSError, ValueError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid SAP CPI request"})

    def _handle_runtime_test(self) -> None:
        try:
            payload = self._read_json_request(12 * 1024 * 1024)
            headers = payload.get("headers", {})
            if not isinstance(headers, dict):
                raise SapCpiError("Request headers must be a JSON object")
            client = RuntimeHttpClient(
                principal=str(payload.get("principal", "")),
                secret=str(payload.get("secret", "")),
                auth_type=str(payload.get("auth_type", "basic")),
                token_url=str(payload.get("token_url", "")),
            )
            response = client.call(
                endpoint=str(payload.get("endpoint", "")),
                xml_body=str(payload.get("body", "")),
                headers={str(key): str(value) for key, value in headers.items()},
            )
            self._json(HTTPStatus.OK, response.to_dict())
        except (SapCpiError, ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _handle_mock_config(self) -> None:
        try:
            payload = self._read_json_request()
            status = int(payload.get("status", 200))
            content_type = str(payload.get("content_type", "application/xml")).strip()
            body = str(payload.get("body", ""))
            if status < 200 or status > 599:
                raise ValueError("Mock status must be between 200 and 599")
            if not content_type or "\n" in content_type or "\r" in content_type:
                raise ValueError("Invalid mock Content-Type")
            self.__class__.mock_config = {"status": status, "content_type": content_type, "body": body}
            self._json(HTTPStatus.OK, {"url": f"http://{self.headers.get('Host', '127.0.0.1:8765')}/mock/receiver"})
        except (SapCpiError, ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _send_mock_response(self) -> None:
        config = self.__class__.mock_config
        self._send(int(config["status"]), str(config["body"]).encode("utf-8"), str(config["content_type"]))

    def _read_json_request(self, max_bytes: int = 1024 * 1024) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise SapCpiError("Invalid request length") from exc
        if length <= 0 or length > max_bytes:
            raise SapCpiError("Invalid SAP CPI request size")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise SapCpiError("Expected a JSON object")
        return payload

    @staticmethod
    def _multipart_files(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
        )
        uploads: list[tuple[str, bytes]] = []
        if not message.is_multipart():
            return uploads
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data" or part.get_param("name", header="content-disposition") != "files":
                continue
            filename = part.get_filename()
            if filename:
                uploads.append((filename, part.get_payload(decode=True) or b""))
        return uploads

    @staticmethod
    def _safe_relative(filename: str) -> Path:
        parts = [part for part in filename.replace("\\", "/").split("/") if part not in {"", "."}]
        if not parts or any(part == ".." for part in parts):
            raise AnalysisError(f"unsafe uploaded path: {filename}")
        return Path(*parts)

    @classmethod
    def _assemble_bundle(cls, bundle: Path, uploads: list[tuple[str, bytes]]) -> list[tuple[str, Path]]:
        loose_root = bundle / "unzipped"
        zip_root = bundle / "packages"
        packages: list[tuple[str, Path]] = []
        has_loose_files = False
        for index, (filename, content) in enumerate(uploads, start=1):
            relative = cls._safe_relative(filename)
            if relative.suffix.lower() != ".zip":
                has_loose_files = True
                target = (loose_root / relative).resolve()
                if loose_root.resolve() not in target.parents:
                    raise AnalysisError(f"unsafe uploaded path: {filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                continue
            archive_path = bundle / f"upload-{index}.zip"
            archive_path.write_bytes(content)
            destination = (zip_root / f"{index}-{relative.stem}").resolve()
            destination.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    for member in archive.infolist():
                        target = (destination / member.filename).resolve()
                        if destination not in target.parents and target != destination:
                            raise AnalysisError(f"unsafe ZIP member in {filename}: {member.filename}")
                    archive.extractall(destination)
            except zipfile.BadZipFile as exc:
                raise AnalysisError(f"invalid ZIP package: {filename}") from exc
            archive_path.unlink()
            packages.append((relative.name, destination))
        if has_loose_files:
            packages.append(("Unzipped files", loose_root))
        return packages

    @staticmethod
    def _infer_role(filename: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", Path(filename).stem.lower()).split()
        if any(token in {"req", "request"} for token in normalized):
            return "Request"
        if any(token in {"res", "resp", "response"} for token in normalized):
            return "Response"
        return "Unclassified"

    @classmethod
    def _paired_report(cls, analyses: list[tuple[str, str, Analysis]]) -> str:
        role_order = {"Request": 0, "Response": 1, "Unclassified": 2}
        ordered = sorted(analyses, key=lambda item: (role_order[item[1]], item[0].lower()))
        lines = ["# SAP CPI Paired Request/Response Analysis", "", "## Package Relationship", ""]
        lines += ["| Package | Inferred Role | IFlow | Sender | Receiver | Payloads |", "|---|---|---|---|---|---|"]
        for filename, role, analysis in ordered:
            cells = (filename, role, analysis.name, analysis.sender, analysis.receiver, str(len(analysis.payloads)))
            lines.append("| " + " | ".join(Analysis._cell(value) for value in cells) + " |")
        requests = [item for item in ordered if item[1] == "Request"]
        responses = [item for item in ordered if item[1] == "Response"]
        lines += ["", "## End-to-End Interpretation", ""]
        if requests and responses:
            request = requests[0][2]
            response = responses[0][2]
            lines += [
                f"- **Request path:** {request.name} — {request.sender} → {request.receiver}",
                f"- **Response path:** {response.name} — {response.sender} → {response.receiver}",
                "- Request and response requirements and payloads are kept separate below; no route condition from one package is applied to the other.",
                "- The pairing is inferred from the uploaded filenames. Confirm the runtime correlation/header contract if it is implemented outside these artifacts.",
            ]
        else:
            lines.append("- A complete request/response pair could not be inferred from filenames. Use `Req`/`Request` and `Res`/`Response` in package names, or review the unclassified sections below.")
        for filename, role, analysis in ordered:
            lines += ["", f"## {role} Package: `{filename}`", ""]
            nested = re.sub(r"^(#{1,4}) ", lambda match: match.group(1) + "## ", analysis.to_markdown(), flags=re.MULTILINE)
            lines.append(nested.rstrip())
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _analysis_response(analyses: list[tuple[str, str, Analysis]], report: str) -> dict[str, object]:
        payloads: list[dict[str, str]] = []
        sender_paths: list[str] = []
        sender_endpoints: list[dict[str, str]] = []
        request_reply = False
        for package_name, role, analysis in analyses:
            request_reply = request_reply or any("Request-Reply" in step for step in analysis.steps) or any(
                item.kind == "Request-Reply" for item in analysis.config
            )
            sender_configs: dict[str, dict[str, str]] = {}
            for item in analysis.config:
                if item.kind != "Sender Adapter" or not item.value:
                    continue
                sender_configs.setdefault(item.step, {})[item.name.lower()] = item.value
            for step, config in sender_configs.items():
                address = next((config.get(key, "") for key in ("urlpath", "address", "endpoint", "endpointname") if config.get(key)), "")
                if not address or address.startswith(("{{", "${")):
                    continue
                adapter = next((config.get(key, "") for key in ("componenttype", "adaptertype", "senderadapter", "transportprotocol") if config.get(key)), "")
                runtime_path = IFlowWebHandler._sender_runtime_path(adapter, address)
                sender_paths.append(runtime_path)
                sender_endpoints.append({
                    "name": step,
                    "adapter": adapter,
                    "configured_address": address,
                    "runtime_path": runtime_path,
                })
            package_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(package_name).stem).strip("-_") or "iflow"
            xml_payloads = [payload for payload in analysis.payloads if payload.format.lower() == "xml"]
            for index, payload in enumerate(xml_payloads, start=1):
                try:
                    document_name = local_name(ET.fromstring(payload.body).tag)
                except ET.ParseError:
                    document_name = Path(payload.source).stem
                schema_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", document_name).strip("-_") or "payload"
                payloads.append({
                    "filename": f"{package_stem}-{schema_stem}-{index:02d}.xml",
                    "role": role,
                    "scenario": payload.scenario,
                    "body": payload.body,
                })
        return {
            "report": report,
            "payloads": payloads,
            "test": {
                "request_reply": request_reply,
                "sender_paths": list(dict.fromkeys(sender_paths)),
                "sender_endpoints": list({(item["adapter"], item["configured_address"], item["runtime_path"]): item for item in sender_endpoints}.values()),
            },
        }

    @staticmethod
    def _sender_runtime_path(adapter: str, address: str) -> str:
        """Translate an IFlow sender adapter address into its deployed runtime path."""
        configured = address.strip()
        parsed = urlparse(configured if "://" in configured else f"https://runtime.invalid/{configured.lstrip('/')}")
        path = parsed.path.strip("/")
        adapter_name = re.sub(r"[^a-z0-9]+", "", adapter.lower())
        prefixes = {
            "http": "http",
            "https": "http",
            "rest": "http",
            "soap": "cxf",
            "soap11": "cxf",
            "soap12": "cxf",
            "as2": "as2",
            "odata": "odata",
        }
        prefix = prefixes.get(adapter_name, "")
        if prefix and not (path == prefix or path.startswith(prefix + "/")):
            path = f"{prefix}/{path}" if path else prefix
        return f"/{path}" if path else "/"

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str, download_name: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the IFlow analyzer web app locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), IFlowWebHandler)
    print(f"IFlow Test Payload Generator running at http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
