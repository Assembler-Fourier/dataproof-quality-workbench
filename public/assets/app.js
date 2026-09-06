"use strict";

const $ = (id) => document.getElementById(id);
const state = {file: null, profile: null, contract: null, report: null, column: null, busy: false};
const names = {non_null: "Non-null", type: "Type", unique: "Unique", allowed_values: "Allowed set", minimum: "Minimum", maximum: "Maximum", required_column: "Missing column", unexpected_column: "Unexpected column"};
const number = (value) => new Intl.NumberFormat("en").format(value);

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
}

function setBusy(busy) {
  state.busy = busy;
  document.querySelectorAll("button, input, select, textarea").forEach((control) => { control.disabled = busy; });
  $("workspace").setAttribute("aria-busy", String(busy));
  $("run-button").textContent = busy ? "Processing…" : "Run validation →";
  if (!busy) {
    $("export-report").disabled = !state.report;
    $("export-rejected").disabled = !state.report || state.report.rejected_rows === 0;
    $("run-button").disabled = !state.file || !state.contract;
    $("export-contract").disabled = !state.contract;
    setBoundsEnabled();
  }
}

async function request(path, form) {
  const response = await fetch(path, {method: "POST", body: form});
  if (!response.ok) {
    let detail = "The request failed. Please try again.";
    try { const body = await response.json(); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch { /* Proxy errors may not be JSON. */ }
    throw new Error(detail);
  }
  return response;
}

function formData(withContract = false) {
  const form = new FormData();
  form.append("file", state.file);
  if (withContract) form.append("contract", JSON.stringify(state.contract));
  return form;
}

function defaultRule(type = "string") {
  return {type, required: true, nullable: true, unique: false, allowed_values: null, minimum: null, maximum: null};
}

function clearReport() {
  state.report = null;
  $("report-content").hidden = true;
  $("report-empty").hidden = false;
  $("report-empty").textContent = "Rules or data changed. Run validation to create a fresh report.";
  $("result-status").textContent = "Ready";
  $("result-status").className = "status-pill neutral";
  $("export-report").disabled = true;
  $("export-rejected").disabled = true;
}

function renderProfile() {
  const profile = state.profile;
  $("filename").textContent = state.file.name;
  $("file-detail").textContent = `${number(profile.row_count)} records · ${number(profile.column_count)} columns · ${(state.file.size / 1024).toFixed(1)} KiB`;
  $("row-count").textContent = number(profile.row_count);
  $("column-count").textContent = number(profile.column_count);
  $("null-count").textContent = number(profile.columns.reduce((sum, column) => sum + column.null_count, 0));
  $("profile-body").replaceChildren(...profile.columns.map((column) => {
    const row = element("tr");
    row.classList.toggle("selected", column.name === state.column);
    const cell = element("td");
    const button = element("button", column.name, "column-button");
    button.type = "button";
    button.disabled = state.busy;
    button.setAttribute("aria-label", `Edit rules for ${column.name}`);
    button.addEventListener("click", () => {
      if (!Object.hasOwn(state.contract.columns, column.name)) {
        state.contract.columns[column.name] = defaultRule(column.inferred_type);
        clearReport();
      }
      state.column = column.name;
      renderContract();
      renderProfile();
    });
    cell.append(button); row.append(cell);
    const type = element("td"); type.append(element("span", column.inferred_type, "type-tag")); row.append(type);
    row.append(element("td", column.null_count, column.null_count ? "null-warning" : ""), element("td", number(column.unique_count)));
    return row;
  }));
  const head = element("tr");
  profile.columns.forEach((column) => head.append(element("th", column.name)));
  $("preview-head").replaceChildren(head);
  $("preview-body").replaceChildren(...profile.preview.map((values) => {
    const row = element("tr"); values.forEach((value) => row.append(element("td", value || "—"))); return row;
  }));
}

function renderContract() {
  const contract = state.contract;
  const columns = Object.keys(contract.columns);
  if (!columns.includes(state.column)) state.column = columns[0];
  $("contract-name").value = contract.name;
  $("extra-columns").checked = contract.allow_extra_columns;
  $("rule-count").textContent = `${columns.length} columns`;
  $("column-select").replaceChildren(...columns.map((name) => {
    const option = element("option", name); option.value = name; return option;
  }));
  $("column-select").value = state.column;
  renderEditor();
}

function renderEditor() {
  if (!state.contract || !state.column) return;
  const rule = state.contract.columns[state.column];
  $("column-type").value = rule.type;
  $("required-column").checked = rule.required;
  $("non-null").checked = !rule.nullable;
  $("unique").checked = rule.unique;
  $("minimum").value = rule.minimum ?? "";
  $("maximum").value = rule.maximum ?? "";
  $("allowed-values").value = rule.allowed_values?.join("\n") ?? "";
  const column = state.profile?.columns.find((item) => item.name === state.column);
  $("observed-range").textContent = column?.minimum !== null && column?.minimum !== undefined ? `${column.minimum} → ${column.maximum}` : "—";
  setBoundsEnabled();
}

function setBoundsEnabled() {
  const enabled = ["integer", "number"].includes($("column-type").value);
  $("minimum").disabled = state.busy || !enabled;
  $("maximum").disabled = state.busy || !enabled;
}

function updateRule() {
  if (!state.contract || !state.column) return;
  const type = $("column-type").value;
  const numeric = ["integer", "number"].includes(type);
  const allowed = $("allowed-values").value.split("\n").map((value) => value.trim()).filter(Boolean);
  state.contract.columns[state.column] = {
    type, required: $("required-column").checked, nullable: !$("non-null").checked,
    unique: $("unique").checked, allowed_values: allowed.length ? [...new Set(allowed)] : null,
    minimum: numeric && $("minimum").value !== "" ? $("minimum").value : null,
    maximum: numeric && $("maximum").value !== "" ? $("maximum").value : null,
  };
  setBoundsEnabled();
  clearReport();
}

function renderFindings() {
  const selected = $("rule-filter").value;
  const violations = state.report.violations.filter((violation) => selected === "all" || violation.rule === selected);
  const rows = violations.map((violation) => {
    const row = element("tr");
    row.append(element("td", violation.row === null ? "Schema" : `#${violation.row}`), element("td", violation.column));
    const rule = element("td"); rule.append(element("span", names[violation.rule] || violation.rule, "rule-chip")); row.append(rule);
    const value = element("td"); value.append(element("code", violation.value === null ? "missing" : (violation.value.trim() ? violation.value + (violation.value_truncated ? "… [truncated]" : "") : "empty"), "value-code")); row.append(value);
    row.append(element("td", violation.message)); return row;
  });
  if (!rows.length) {const row = element("tr"); const cell = element("td", state.report.passed ? "All records meet the contract. Ready for the next step." : "No displayed findings match this filter."); cell.colSpan = 5; row.append(cell); rows.push(row);}
  $("findings-body").replaceChildren(...rows);
}

function renderReport() {
  const report = state.report;
  $("report-empty").hidden = true; $("report-content").hidden = false;
  $("result-status").textContent = report.passed ? "Passed" : "Needs attention";
  $("result-status").className = `status-pill ${report.passed ? "passed" : "failed"}`;
  $("quality-percent").textContent = `${report.quality_percent}%`;
  $("quality-fraction").textContent = `${number(report.valid_rows)} of ${number(report.total_rows)} records`;
  $("quality-progress").value = report.quality_percent;
  $("valid-rows").textContent = number(report.valid_rows);
  $("rejected-rows").textContent = number(report.rejected_rows);
  $("violation-count").textContent = number(report.total_violations);
  $("findings-caption").textContent = report.violations_truncated ? `Showing the first ${report.violations.length} of ${number(report.total_violations)} violations. Counts and rejected exports include every record.` : report.schema_violations ? "Schema failures reject every record. Fix the missing or unexpected columns first." : `${number(report.total_violations)} violations across ${number(report.rejected_rows)} rejected records.`;
  const all = element("option", "All rules"); all.value = "all";
  $("rule-filter").replaceChildren(all, ...Object.entries(report.rule_counts).map(([rule, count]) => {
    const option = element("option", `${names[rule] || rule} (${number(count)})`); option.value = rule; return option;
  }));
  $("rule-summary").replaceChildren(...Object.entries(report.rule_counts).map(([rule, count]) => {
    const badge = element("span", names[rule] || rule, "rule-badge"); badge.append(element("strong", count)); return badge;
  }));
  renderFindings();
}

async function runValidation() {
  const response = await request("/api/validate", formData(true));
  state.report = await response.json();
  renderReport();
  notice(state.report.passed ? "Validation passed. Every record meets the current contract." : `Validation complete: ${number(state.report.rejected_rows)} rejected records. Inspect the findings below or export them for cleanup.`);
}

async function loadFile(file, contract = null) {
  if (file.size > 2 * 1024 * 1024) throw new Error("CSV exceeds the 2 MiB limit.");
  const form = new FormData(); form.append("file", file);
  const response = await request("/api/profile", form);
  const profile = await response.json();
  state.file = file; state.profile = profile;
  state.contract = contract || {name: `${file.name.replace(/\.csv$/i, "")} contract`, version: 1, allow_extra_columns: true, columns: Object.fromEntries(profile.columns.map((column) => [column.name, defaultRule(column.inferred_type)]))};
  state.column = Object.keys(state.contract.columns)[0];
  clearReport(); renderProfile(); renderContract();
  notice("Dataset profiled. Inferred types describe this file; set your expected types and rules before validation.");
  if (contract) await runValidation();
}

async function task(action) {
  if (state.busy) return;
  setBusy(true);
  try { await action(); } catch (error) { notice(error.message || "Something went wrong. Please try again.", true); }
  finally { setBusy(false); }
}

async function loadSample() {
  const responses = await Promise.all([fetch("/samples/orders.csv"), fetch("/contracts/orders.json")]);
  if (responses.some((response) => !response.ok)) throw new Error("The sample files could not be loaded. Try uploading a CSV.");
  const [csv, contract] = await Promise.all([responses[0].text(), responses[1].json()]);
  await loadFile(new File([csv], "orders-sample.csv", {type: "text/csv"}), contract);
  notice("Example loaded: 12 orders with intentional defects. Edit a rule and run again, or upload your own CSV.");
}

function download(content, filename, type) {
  const blob = content instanceof Blob ? content : new Blob([content], {type});
  const url = URL.createObjectURL(blob);
  const anchor = element("a"); anchor.href = url; anchor.download = filename;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("sample-button").addEventListener("click", () => task(loadSample));
$("file-input").addEventListener("change", (event) => { const file = event.target.files[0]; if (file) task(() => loadFile(file)); event.target.value = ""; });
$("run-button").addEventListener("click", () => task(runValidation));
$("rule-form").addEventListener("submit", (event) => event.preventDefault());
$("rule-form").addEventListener("input", updateRule);
$("contract-name").addEventListener("input", () => { if (state.contract) {state.contract.name = $("contract-name").value; clearReport();} });
$("extra-columns").addEventListener("change", () => { if (state.contract) {state.contract.allow_extra_columns = $("extra-columns").checked; clearReport();} });
$("column-select").addEventListener("change", () => { state.column = $("column-select").value; renderEditor(); renderProfile(); });
$("rule-filter").addEventListener("change", renderFindings);
$("export-contract").addEventListener("click", () => task(async () => {
  await request("/api/validate", formData(true));
  download(JSON.stringify(state.contract, null, 2) + "\n", "dataproof-contract.json", "application/json");
  notice("Contract schema checked and exported. The file is ready to reuse in the API or CLI.");
}));
$("export-report").addEventListener("click", () => download(JSON.stringify(state.report, null, 2) + "\n", "dataproof-report.json", "application/json"));
$("export-rejected").addEventListener("click", () => task(async () => { const response = await request("/api/rejected.csv", formData(true)); download(await response.blob(), "dataproof-rejected.csv"); }));
$("contract-input").addEventListener("change", (event) => {
  const file = event.target.files[0]; event.target.value = "";
  if (!file) return;
  task(async () => {
    if (!state.file) throw new Error("Load a CSV before importing a contract.");
    if (file.size > 100_000) throw new Error("Contract exceeds the 100 KB limit.");
    const content = await file.text();
    let candidate;
    try { candidate = JSON.parse(content); } catch { throw new Error("Contract must contain valid JSON."); }
    for (const rule of Object.values(candidate?.columns || {})) {
      for (const bound of [rule?.minimum, rule?.maximum]) {
        if (typeof bound === "number" && !Number.isSafeInteger(bound)) throw new Error("Use JSON strings for decimal or large numeric bounds to preserve exact precision (for example, \"0.01\").");
      }
    }
    const form = new FormData(); form.append("file", state.file); form.append("contract", content);
    const response = await request("/api/validate", form);
    const report = await response.json();
    candidate = {name: "Untitled contract", version: 1, allow_extra_columns: true, ...candidate, columns: Object.fromEntries(Object.entries(candidate.columns).map(([name, rule]) => [name, {...defaultRule(), ...rule}]))};
    state.contract = candidate; state.report = report; state.column = Object.keys(candidate.columns)[0];
    renderContract(); renderProfile(); renderReport(); notice("Contract imported and validated against the current dataset.");
  });
});
const dropZone = $("drop-zone");
dropZone.addEventListener("dragover", (event) => { event.preventDefault(); if (!state.busy) dropZone.classList.add("dragging"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
dropZone.addEventListener("drop", (event) => { event.preventDefault(); dropZone.classList.remove("dragging"); const file = event.dataTransfer.files[0]; if (file) task(() => loadFile(file)); });
task(loadSample);
