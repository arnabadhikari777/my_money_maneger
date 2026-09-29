// Register the service worker (push notifications + offline fallback).
// It is served from /sw.js so its scope is the whole site. (It used to be
// registered from /static/sw.js, which only covers /static/ - that meant it
// never controlled the app's pages and "Enable reminders" silently hung.)
if ("serviceWorker" in navigator) {
  window.addEventListener("load", async () => {
    try {
      // Remove the old, wrongly-scoped registration if this device has one.
      const regs = await navigator.serviceWorker.getRegistrations();
      for (const r of regs) {
        if (r.scope.endsWith("/static/")) await r.unregister();
      }
      await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    } catch (e) {
      console.error("Service worker registration failed:", e);
    }
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

function withTimeout(promise, ms, message) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(message)), ms)),
  ]);
}

function setPushStatus(text, kind) {
  const el = document.getElementById("push-status");
  if (!el) return;
  el.textContent = text;
  el.className = "push-status " + (kind || "");
}

// Shows whether THIS device currently has reminders switched on.
async function refreshPushStatus() {
  if (!document.getElementById("push-status")) return;
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    setPushStatus("This browser can't receive push notifications. Use Chrome on your phone.", "warn");
    return;
  }
  if (Notification.permission === "denied") {
    setPushStatus("Notifications are blocked for this app. Allow them in Chrome's site settings, then try again.", "warn");
    return;
  }
  try {
    const reg = await withTimeout(navigator.serviceWorker.ready, 6000, "not ready");
    const sub = await reg.pushManager.getSubscription();
    if (sub) setPushStatus("✓ Reminders are on for this device.", "ok");
    else setPushStatus("Reminders are off on this device.", "");
  } catch (e) {
    setPushStatus("Getting ready… reload this page in a moment.", "");
  }
}
document.addEventListener("DOMContentLoaded", refreshPushStatus);

// Turns reminders on for this device. Every failure path tells you what
// went wrong instead of failing silently.
async function enablePushNotifications(vapidPublicKey) {
  try {
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      alert("This browser can't receive push notifications. Please use Chrome on your phone.");
      return;
    }
    if (Notification.permission === "denied") {
      alert("Notifications are blocked for this app. Open Chrome's site settings, allow Notifications, then tap this button again.");
      return;
    }

    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      alert("Permission wasn't granted, so I can't switch reminders on.");
      return;
    }

    setPushStatus("Turning on…", "");
    const reg = await withTimeout(
      navigator.serviceWorker.ready, 10000,
      "The background worker isn't ready yet. Reload the app and try again."
    );

    // A subscription created with an older key pair can't be reused -
    // drop it (and tell the server) before making a fresh one.
    const existing = await reg.pushManager.getSubscription();
    if (existing) {
      const oldEndpoint = existing.endpoint;
      try { await existing.unsubscribe(); } catch (e) {}
      try {
        await fetch("/notifications/unsubscribe", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
          body: JSON.stringify({ endpoint: oldEndpoint }),
        });
      } catch (e) {}
    }

    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
    });

    const res = await fetch("/notifications/subscribe", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(sub),
    });
    if (!res.ok) {
      throw new Error("The server didn't accept the subscription (HTTP " + res.status + ").");
    }

    setPushStatus("✓ Reminders are on for this device.", "ok");
    alert("Reminders are on — I'll let you know before your recharges run out.");
  } catch (e) {
    console.error(e);
    refreshPushStatus();
    alert("Couldn't turn on reminders: " + (e && e.message ? e.message : e));
  }
}

// --- Android "Add to Home Screen" install prompt ---
let deferredInstallPrompt = null;

function isRunningInstalled() {
  return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}

function refreshInstallUI() {
  const btn = document.getElementById("install-app-btn");
  const status = document.getElementById("install-status");
  if (!btn && !status) return;

  if (isRunningInstalled()) {
    if (btn) btn.style.display = "none";
    if (status) status.style.display = "inline-flex";
  } else {
    if (status) status.style.display = "none";
    if (btn) btn.style.display = deferredInstallPrompt ? "block" : "none";
  }
}

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  refreshInstallUI();
});

window.addEventListener("appinstalled", refreshInstallUI);
document.addEventListener("DOMContentLoaded", refreshInstallUI);

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
    // Add Money / Add Account pages: total notes counted = the amount/balance.
    const amountField = document.getElementById("amount") || document.getElementById("opening_balance");
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
