const toast = document.querySelector("#toast");
const nextRun = document.querySelector("#next-run");
const scheduleState = document.querySelector("#schedule-state");
const timeInput = document.querySelector("#time");
const scheduleTimeSummary = document.querySelector("#schedule-time-summary");
const saveScheduleButton = document.querySelector("#save-schedule");
const runNowButton = document.querySelector("#run-now");

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
    return "Disabled";
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

function updateScheduleSummary() {
  if (scheduleTimeSummary) {
    scheduleTimeSummary.textContent = `${timeInput.value || "08:30"} IST`;
  }
}

function scheduleForm() {
  return {
    enabled: document.querySelector("#enabled").checked,
    time: document.querySelector("#time").value,
    timezone: "Asia/Kolkata",
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
  nextRun.textContent = formatIstDateTime(payload.next_run);
  if (scheduleState) {
    scheduleState.textContent = payload.config?.enabled ? "Enabled" : "Disabled";
  }
  updateScheduleSummary();
}

async function saveSchedule() {
  saveScheduleButton.disabled = true;
  try {
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
    nextRun.textContent = formatIstDateTime(payload.next_run);
    if (scheduleState) {
      scheduleState.textContent = scheduleForm().enabled ? "Enabled" : "Disabled";
    }
    updateScheduleSummary();
    showToast("Schedule saved.");
  } catch {
    showToast("Could not reach the schedule server.");
  } finally {
    saveScheduleButton.disabled = false;
  }
}

async function runNow() {
  runNowButton.disabled = true;
  try {
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
  } catch {
    showToast("Could not reach the schedule server.");
  } finally {
    runNowButton.disabled = false;
  }
}

saveScheduleButton.addEventListener("click", saveSchedule);
runNowButton.addEventListener("click", runNow);
timeInput.addEventListener("input", updateScheduleSummary);

updateScheduleSummary();
refreshSchedule();
window.setInterval(refreshSchedule, 5000);
