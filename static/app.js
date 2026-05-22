const statusValue = document.querySelector("#status-value");
const statusDot = document.querySelector("#status-dot");
const logs = document.querySelector("#logs");
const stopButton = document.querySelector("#stop-button");
const toast = document.querySelector("#toast");
const runButtons = [...document.querySelectorAll("[data-workflow]")];
const nextRunValue = document.querySelector("#next-run-value");
const copyLogButton = document.querySelector("#copy-log");
const openLogSheetButton = document.querySelector("#open-log-sheet");
const logLineCount = document.querySelector("#log-line-count");
const startDateInput = document.querySelector("#start_date");
const endDateInput = document.querySelector("#end_date");
const dateWindowLabel = document.querySelector("#date-window-label");
const LOG_SPREADSHEET_ID = "1veHtzoByPQfD7CDynmxJOTiH2ZuksqkxUnmG96alwYE";

function formData() {
  return {
    target_spreadsheet_id: document.querySelector("#target_spreadsheet_id").value,
    start_date: document.querySelector("#start_date").value,
    end_date: document.querySelector("#end_date").value,
    skip_snapshot: document.querySelector("#skip_snapshot").checked,
    dry_run: document.querySelector("#dry_run").checked,
    keep_all_candidates: document.querySelector("#keep_all_candidates").checked,
    keep_all_jobs: document.querySelector("#keep_all_jobs").checked,
  };
}

function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 4500);
}

function formatIstDateTime(value) {
  if (!value) {
    return "Not scheduled";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("en-IN", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "Asia/Kolkata",
    timeZoneName: "short",
  }).format(parsed);
}

function updateDateWindowLabel() {
  if (!dateWindowLabel) {
    return;
  }
  const startDate = startDateInput.value || "Start date";
  const endDate = endDateInput.value || "End date";
  dateWindowLabel.textContent = `${startDate} to ${endDate}`;
}

function setRunning(isRunning) {
  runButtons.forEach((button) => {
    button.disabled = isRunning;
  });
  stopButton.disabled = !isRunning;
}

function updateRequirements(requirements) {
  const list = document.querySelector("#requirements");
  list.innerHTML = "";
  Object.entries(requirements).forEach(([key, ok]) => {
    const item = document.createElement("li");
    item.className = ok ? "ok" : "warn";
    item.textContent = key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
    list.appendChild(item);
  });
}

async function startWorkflow(workflow) {
  setRunning(true);
  const clickedButton = runButtons.find((button) => button.dataset.workflow === workflow);
  const originalLabel = clickedButton?.textContent;
  if (clickedButton) {
    clickedButton.textContent = "Starting...";
  }
  try {
    const response = await fetch(`/run/${workflow}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formData()),
    });
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.error || "Could not start workflow.");
      setRunning(false);
      return;
    }
    showToast("Workflow started.");
    await refreshStatus();
  } catch {
    showToast("Could not reach the workflow server.");
    setRunning(false);
  } finally {
    if (clickedButton && originalLabel) {
      clickedButton.textContent = originalLabel;
    }
  }
}

async function stopWorkflow() {
  const response = await fetch("/stop", { method: "POST" });
  const payload = await response.json();
  if (!response.ok) {
    showToast(payload.error || "Could not stop workflow.");
    return;
  }
  showToast("Stop requested.");
  await refreshStatus();
}

async function refreshStatus() {
  const response = await fetch("/status");
  const payload = await response.json();
  const state = payload.status || "Ready";

  statusValue.textContent = payload.workflow ? `${payload.workflow}: ${state}` : state;
  statusDot.classList.toggle("running", payload.running);
  statusDot.classList.toggle("failed", state === "Failed");
  setRunning(payload.running);
  updateRequirements(payload.requirements || {});
  if (nextRunValue) {
    nextRunValue.textContent = formatIstDateTime(payload.schedule?.next_run);
  }

  if (payload.logs && payload.logs.length) {
    logs.textContent = payload.logs.join("");
    logs.scrollTop = logs.scrollHeight;
    if (logLineCount) {
      logLineCount.textContent = `${payload.logs.length} lines`;
    }
  }
}

runButtons.forEach((button) => {
  button.addEventListener("click", () => startWorkflow(button.dataset.workflow));
});

stopButton.addEventListener("click", stopWorkflow);

document.querySelector("#refresh-status").addEventListener("click", refreshStatus);

copyLogButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(logs.textContent);
    showToast("Log copied.");
  } catch {
    showToast("Could not copy log from this browser.");
  }
});

openLogSheetButton.addEventListener("click", () => {
  window.open(`https://docs.google.com/spreadsheets/d/${LOG_SPREADSHEET_ID}/edit`, "_blank", "noopener");
});

document.querySelector("#open-sheet").addEventListener("click", () => {
  const sheetId = document.querySelector("#target_spreadsheet_id").value.trim();
  if (!sheetId) {
    showToast("Target Google Sheet ID is required.");
    return;
  }
  window.open(`https://docs.google.com/spreadsheets/d/${sheetId}/edit`, "_blank", "noopener");
});

startDateInput.addEventListener("input", updateDateWindowLabel);
endDateInput.addEventListener("input", updateDateWindowLabel);
updateDateWindowLabel();
refreshStatus();
window.setInterval(refreshStatus, 2000);
