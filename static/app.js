const statusValue   = document.querySelector("#status-value");
const statusDot     = document.querySelector("#status-dot");
const logs          = document.querySelector("#logs");
const stopButton    = document.querySelector("#stop-button");
const toast         = document.querySelector("#toast");
const runButtons    = [...document.querySelectorAll("[data-workflow]")];
const nextRunValue  = document.querySelector("#next-run-value");
const copyLogBtn    = document.querySelector("#copy-log");
const openLogBtn    = document.querySelector("#open-log-sheet");
const logLineCount  = document.querySelector("#log-line-count");
const startDateInput = document.querySelector("#start_date");
const endDateInput   = document.querySelector("#end_date");
const dateWindowLabel = document.querySelector("#date-window-label");
const dataBadge     = document.querySelector("#data-badge");
const asgnBadge     = document.querySelector("#asgn-badge");
const LOG_SPREADSHEET_ID = "1veHtzoByPQfD7CDynmxJOTiH2ZuksqkxUnmG96alwYE";

function formData() {
  return {
    target_spreadsheet_id: document.querySelector("#target_spreadsheet_id").value,
    start_date:            document.querySelector("#start_date").value,
    end_date:              document.querySelector("#end_date").value,
    skip_snapshot:         document.querySelector("#skip_snapshot").checked,
    dry_run:               document.querySelector("#dry_run").checked,
    keep_all_candidates:   document.querySelector("#keep_all_candidates").checked,
    keep_all_jobs:         document.querySelector("#keep_all_jobs").checked,
  };
}

function showToast(msg) {
  toast.textContent = msg;
  toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { toast.hidden = true; }, 4500);
}

function formatIst(value) {
  if (!value) return "Not scheduled";
  const d = new Date(value);
  if (isNaN(d)) return value;
  return new Intl.DateTimeFormat("en-IN", {
    weekday: "short", day: "2-digit", month: "short",
    hour: "numeric", minute: "2-digit", hour12: true,
    timeZone: "Asia/Kolkata", timeZoneName: "short",
  }).format(d);
}

function updateDateWindowLabel() {
  if (dateWindowLabel) {
    dateWindowLabel.textContent = `${startDateInput.value || "Start"} → ${endDateInput.value || "End"}`;
  }
}

function setWorkflowBadge(workflow, state) {
  const badge = workflow === "data" ? dataBadge : asgnBadge;
  if (!badge) return;
  badge.className = "status-badge " + (
    state === "Running" ? "running" :
    state === "Completed" ? "done" :
    state === "Failed" ? "failed" : "idle"
  );
  badge.textContent = state === "Running" ? "Running" : state === "Completed" ? "Done" : state === "Failed" ? "Failed" : "Idle";
}

function setRunning(running) {
  runButtons.forEach(b => { b.disabled = running; });
  if (stopButton) stopButton.disabled = !running;
}

function updateRequirements(req) {
  const list = document.querySelector("#requirements");
  if (!list) return;
  list.innerHTML = "";
  Object.entries(req).forEach(([key, ok]) => {
    const li = document.createElement("li");
    li.className = "req-item " + (ok ? "ok" : "warn");
    li.innerHTML = `${key.replaceAll("_", " ").replace(/\b\w/g, l => l.toUpperCase())}
      <span class="req-badge">${ok ? "OK" : "Needs setup"}</span>`;
    list.appendChild(li);
  });
}

async function startWorkflow(workflow) {
  setRunning(true);
  const btn = runButtons.find(b => b.dataset.workflow === workflow);
  const orig = btn?.textContent;
  if (btn) btn.textContent = "Starting…";
  try {
    const res = await fetch(`/run/${workflow}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formData()),
    });
    const data = await res.json();
    if (!res.ok) { showToast(data.error || "Could not start."); setRunning(false); return; }
    showToast("Workflow started.");
    setWorkflowBadge(workflow, "Running");
    await refreshStatus();
  } catch {
    showToast("Could not reach the server.");
    setRunning(false);
  } finally {
    if (btn && orig) btn.textContent = orig;
  }
}

async function stopWorkflow() {
  const res = await fetch("/stop", { method: "POST" });
  const data = await res.json();
  if (!res.ok) { showToast(data.error || "Could not stop."); return; }
  showToast("Stop requested.");
  await refreshStatus();
}

async function refreshStatus() {
  try {
    const res = await fetch("/status");
    const payload = await res.json();
    const state = payload.status || "Ready";
    const wf = payload.workflow || "";

    if (statusValue) statusValue.textContent = wf ? `${wf}: ${state}` : state;
    if (statusDot) {
      statusDot.classList.toggle("running", payload.running);
      statusDot.classList.toggle("failed", state === "Failed");
    }

    if (wf === "Data Workflow") setWorkflowBadge("data", state);
    if (wf === "Assignments")   setWorkflowBadge("assignments", state);
    if (!payload.running) {
      if (state === "Completed" && wf === "Data Workflow") setWorkflowBadge("data", "Completed");
      if (state === "Completed" && wf === "Assignments")   setWorkflowBadge("assignments", "Completed");
    }

    setRunning(payload.running);
    if (payload.requirements) updateRequirements(payload.requirements);
    if (nextRunValue) nextRunValue.textContent = formatIst(payload.schedule?.next_run);

    const scheduleDisplay = document.querySelector("#schedule-time-display");
    if (scheduleDisplay && payload.schedule?.time) {
      scheduleDisplay.textContent = payload.schedule.time + " IST";
    }

    if (payload.logs?.length) {
      logs.textContent = payload.logs.join("");
      logs.scrollTop = logs.scrollHeight;
      if (logLineCount) logLineCount.textContent = `${payload.logs.length} lines`;
    }
  } catch { /* silent – network may be unavailable */ }
}

runButtons.forEach(b => b.addEventListener("click", () => startWorkflow(b.dataset.workflow)));
if (stopButton) stopButton.addEventListener("click", stopWorkflow);
if (document.querySelector("#refresh-status")) {
  document.querySelector("#refresh-status").addEventListener("click", refreshStatus);
}

if (copyLogBtn) {
  copyLogBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(logs.textContent);
      showToast("Log copied.");
    } catch { showToast("Could not copy log."); }
  });
}

if (openLogBtn) {
  openLogBtn.addEventListener("click", () => {
    window.open(`https://docs.google.com/spreadsheets/d/${LOG_SPREADSHEET_ID}/edit`, "_blank", "noopener");
  });
}

const openSheetBtn = document.querySelector("#open-sheet");
if (openSheetBtn) {
  openSheetBtn.addEventListener("click", () => {
    const id = document.querySelector("#target_spreadsheet_id").value.trim();
    if (!id) { showToast("Target Sheet ID is required."); return; }
    window.open(`https://docs.google.com/spreadsheets/d/${id}/edit`, "_blank", "noopener");
  });
}

if (startDateInput) startDateInput.addEventListener("input", updateDateWindowLabel);
if (endDateInput)   endDateInput.addEventListener("input", updateDateWindowLabel);
updateDateWindowLabel();
refreshStatus();
setInterval(refreshStatus, 2000);
