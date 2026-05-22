const toast = document.querySelector("#toast");
const nextRun = document.querySelector("#next-run");
const scheduleState = document.querySelector("#schedule-state");

function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 4500);
}

function scheduleForm() {
  return {
    enabled: document.querySelector("#enabled").checked,
    time: document.querySelector("#time").value,
    timezone: document.querySelector("#timezone").value,
    target_spreadsheet_id: document.querySelector("#target_spreadsheet_id").value,
    skip_snapshot: document.querySelector("#skip_snapshot").checked,
    dry_run: document.querySelector("#dry_run").checked,
    keep_all_candidates: document.querySelector("#keep_all_candidates").checked,
    keep_all_jobs: document.querySelector("#keep_all_jobs").checked,
  };
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

async function refreshSchedule() {
  const response = await fetch("/api/schedule");
  const payload = await response.json();
  nextRun.textContent = payload.next_run || "Disabled";
  if (scheduleState) {
    scheduleState.textContent = payload.config?.enabled ? "Enabled" : "Disabled";
  }
}

async function saveSchedule() {
  const response = await fetch("/api/schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scheduleForm()),
  });
  const payload = await response.json();
  if (!response.ok) {
    showToast(payload.error || "Could not save schedule.");
    return;
  }
  nextRun.textContent = payload.next_run || "Disabled";
  if (scheduleState) {
    scheduleState.textContent = scheduleForm().enabled ? "Enabled" : "Disabled";
  }
  showToast("Schedule saved.");
}

async function runNow() {
  const saveResponse = await fetch("/api/schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scheduleForm()),
  });
  const savePayload = await saveResponse.json();
  if (!saveResponse.ok) {
    showToast(savePayload.error || "Could not save schedule.");
    return;
  }

  const response = await fetch("/api/schedule/run-now", { method: "POST" });
  const payload = await response.json();
  if (!response.ok) {
    showToast(payload.error || "Could not start scheduled chain.");
    return;
  }
  showToast("Scheduled chain started.");
  window.location.href = "/";
}

document.querySelector("#save-schedule").addEventListener("click", saveSchedule);
document.querySelector("#run-now").addEventListener("click", runNow);

refreshSchedule();
window.setInterval(refreshSchedule, 5000);
