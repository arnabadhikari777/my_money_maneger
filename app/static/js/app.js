// Register the service worker for offline support + push notifications.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/static/sw.js").catch(console.error);
  });
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

function csrfToken() {
  const el = document.querySelector('meta[name="csrf-token"]');
  return el ? el.content : "";
}

async function enablePushNotifications(vapidPublicKey) {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    alert("Push notifications aren't supported in this browser.");
    return;
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return;

  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
  });

  await fetch("/notifications/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
    body: JSON.stringify(sub),
  });
  alert("Reminders enabled — I'll let you know before your recharges run out.");
}

// --- Android "Add to Home Screen" install prompt ---
let deferredInstallPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  const btn = document.getElementById("install-app-btn");
  if (btn) btn.style.display = "block";
});

function installApp() {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  deferredInstallPrompt = null;
}

// --- Cash denomination counters ---
// `group` is a prefix like "denom" (Add Money), "given" or "change" (Add Expense paid in cash).
function adjustDenom(group, value, delta) {
  const input = document.getElementById(group + "_" + value);
  if (!input) return;
  let v = Math.max(0, parseInt(input.value || "0", 10) + delta);

  const max = input.dataset.max !== undefined ? parseInt(input.dataset.max, 10) : null;
  const warnEl = document.getElementById("cash-max-warning");
  if (max !== null && v > max) {
    v = max;
    if (warnEl) {
      warnEl.style.display = "block";
      clearTimeout(warnEl._hideTimer);
      warnEl._hideTimer = setTimeout(() => { warnEl.style.display = "none"; }, 2500);
    }
  }

  input.value = v;
  const qtyEl = document.getElementById("qty_" + group + "_" + value);
  if (qtyEl) qtyEl.textContent = v;
  recalcGroupTotal(group);
}

function groupTotal(group) {
  const rows = document.querySelectorAll(`[data-group="${group}"]`);
  let total = 0;
  rows.forEach((row) => {
    const value = parseInt(row.getAttribute("data-denom"), 10);
    const input = document.getElementById(group + "_" + value);
    total += value * parseInt((input && input.value) || "0", 10);
  });
  return total;
}

function recalcGroupTotal(group) {
  const total = groupTotal(group);
  const totalEl = document.getElementById(group + "-total-value");
  if (totalEl) totalEl.textContent = total.toLocaleString("en-IN");

  if (group === "denom") {
    // Add Money page: total notes counted = the amount.
    const amountField = document.getElementById("amount");
    if (amountField && total > 0) amountField.value = total;
  }
  if (group === "given" || group === "change") {
    // Add Expense page: amount = notes given - change received back.
    const given = groupTotal("given");
    const change = groupTotal("change");
    const net = given - change;
    const netEl = document.getElementById("cash-net-value");
    if (netEl) netEl.textContent = net.toLocaleString("en-IN");
    const amountField = document.getElementById("amount");
    if (amountField && given > 0) amountField.value = net > 0 ? net : "";
    const warnEl = document.getElementById("cash-net-warning");
    if (warnEl) warnEl.style.display = (given > 0 && net <= 0) ? "block" : "none";
  }
}

function togglePaymentMethodUI() {
  const select = document.getElementById("payment_method");
  const cashBox = document.getElementById("cash-denom-box");
  if (!select || !cashBox) return;
  cashBox.style.display = select.value === "Cash" ? "block" : "none";
}

// --- Category -> subcategory dependent dropdown on the expense form ---
async function loadSubcategories(categoryId, selectedId) {
  const subSelect = document.getElementById("subcategory_id");
  if (!subSelect) return;
  subSelect.innerHTML = '<option value="0">— none —</option>';
  if (!categoryId) return;
  const res = await fetch(`/subcategories/${categoryId}.json`);
  const subs = await res.json();
  subs.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.name;
    if (selectedId && String(selectedId) === String(s.id)) opt.selected = true;
    subSelect.appendChild(opt);
  });
}
