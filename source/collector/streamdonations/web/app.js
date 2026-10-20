"use strict";

const PAGE_SIZE = 100;
const HIGHLIGHT_MS = 60_000;
const DEFAULT_CURRENCY = "USD";

const state = {
  meta: null,
  settings: null,
  rows: [],
  total: 0,
  selected: null,
  filters: {
    platform: "",
    q: "",
    hideReceipts: true,
    offset: 0,
    limit: PAGE_SIZE,
  },
  stream: null,
  presets: [],
  blueScripts: [],
};

const highlightTimers = new Map();
let refreshSerial = 0;
let editingId = null;
let editingUsd = 0;
let streamChain = Promise.resolve();
let lastTabClick = null;
let nameDialogRequestedAt = 0;
let ledgerNameSerial = 0;

const TIER_PLACEHOLDERS = ["Bronze", "Silver", "Gold", "", ""];

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

const PLATFORM_ICONS = {
  youtube: "/icons/youtube.svg",
  rumble: "/icons/rumble.svg",
  kick: "/icons/kick.svg",
};
const PLATFORM_NAMES = {
  youtube: "YouTube",
  rumble: "Rumble",
  kick: "Kick",
  other: "Other",
};

function platformKey(value) {
  const text = String(value || "").trim().toLowerCase();
  if (PLATFORM_NAMES[text]) return text;
  return Object.keys(PLATFORM_NAMES).find((id) => PLATFORM_NAMES[id].toLowerCase() === text) || "";
}

function platformMark(id) {
  const icon = PLATFORM_ICONS[id];
  if (!icon) return null;
  const mark = document.createElement("img");
  mark.className = "platform-mark";
  mark.src = icon;
  mark.alt = "";
  mark.setAttribute("aria-hidden", "true");
  return mark;
}

function platformLabel(id, label) {
  const key = platformKey(id) || platformKey(label);
  const name = document.createElement("span");
  name.className = "platform-name";
  if (key) name.dataset.platform = key;
  const mark = platformMark(key);
  if (mark) name.appendChild(mark);
  name.appendChild(document.createTextNode(label || PLATFORM_NAMES[key] || id || ""));
  return name;
}

function fillPlatformPhrase(node, platform, phrase) {
  if (!node) return;
  const key = platformKey(platform);
  const name = PLATFORM_NAMES[key] || "";
  const text = phrase || "";
  node.replaceChildren();
  const mark = platformMark(key);
  if (mark) node.appendChild(mark);
  if (name && text.toLowerCase().startsWith(name.toLowerCase())) {
    const colored = document.createElement("span");
    colored.className = "platform-name";
    colored.dataset.platform = key;
    colored.textContent = text.slice(0, name.length);
    node.append(colored, document.createTextNode(text.slice(name.length)));
    return;
  }
  node.appendChild(document.createTextNode(text));
}

function icon(name) {
  const paths = {
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"></rect><path d="M5 15V5a2 2 0 0 1 2-2h8"></path>',
    x: '<path d="M7 7l10 10M17 7 7 17"></path>',
    eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"></path><circle cx="12" cy="12" r="2.5"></circle>',
    "eye-off": '<path d="M3 4l18 16"></path><path d="M10.6 6.2A11 11 0 0 1 12 6c6.5 0 10 6 10 6a18 18 0 0 1-3.1 3.7"></path><path d="M6.2 6.9C3.6 8.7 2 12 2 12s3.5 6 10 6c1.2 0 2.4-.2 3.5-.6"></path><path d="M9.9 9.9a2.5 2.5 0 0 0 3.2 3.2"></path>',
    trash: '<path d="M4 7h16"></path><path d="M9 7V5h6v2"></path><path d="M7 7l1 12h8l1-12"></path><path d="M10 11v5M14 11v5"></path>',
    pencil: '<path d="M12 20h9"></path><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4 11.5-11.5z"></path>',
    check: '<path d="M5 12.5 9.5 17 19 7"></path>',
    plus: '<path d="M12 5v14M5 12h14"></path>',
    plug: '<path d="M9 7V3.5M15 7V3.5"></path><path d="M7.5 7h9v5a4.5 4.5 0 0 1-9 0V7z"></path><path d="M12 16.5V21"></path>',
    unplug: '<path d="M9 6V2.5M15 6V2.5"></path><path d="M7.5 6h9v4.2a4.5 4.5 0 0 1-9 0V6z"></path><path d="M12 15v2M12 19.5V22"></path>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"></path>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>',
    send: '<path d="M5 12h14"></path><path d="M13 6l6 6-6 6"></path>',
    chevron: '<path d="M9 6l6 6-6 6"></path>',
  };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "icon");
  svg.innerHTML = paths[name] || "";
  return svg;
}

function closeTabMenus() {
  document.querySelectorAll(".stream-tab-menu").forEach((menu) => {
    menu.hidden = true;
    const button = menu.parentElement && menu.parentElement.querySelector(".stream-tab-menu-button");
    if (button) button.setAttribute("aria-expanded", "false");
  });
}

function closeMenu() {
  for (const [menuId, buttonId] of [
    ["app-menu", "app-menu-button"],
    ["export-menu", "export-button"],
    ["ledger-help", "ledger-help-button"],
  ]) {
    const menu = $(menuId);
    const button = $(buttonId);
    if (!menu || !button) continue;
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
  }
  closeTabMenus();
}

function placeStreamTabMenu(button, menu) {
  menu.hidden = false;
  menu.style.visibility = "hidden";
  const rect = button.getBoundingClientRect();
  const menuRect = menu.getBoundingClientRect();
  let left = rect.right - menuRect.width;
  if (left < 8) left = Math.max(8, rect.left);
  if (left + menuRect.width > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - 8 - menuRect.width);
  }
  let top = rect.bottom + 6;
  if (top + menuRect.height > window.innerHeight - 8) {
    top = Math.max(8, rect.top - menuRect.height - 6);
  }
  menu.style.left = left + "px";
  menu.style.top = top + "px";
  menu.style.visibility = "";
}

function toggleMenu(menuId, buttonId) {
  const menu = $(menuId);
  const button = $(buttonId);
  if (!menu || !button) return;
  const willOpen = menu.hidden;
  closeMenu();
  if (!willOpen) return;
  menu.hidden = false;
  button.setAttribute("aria-expanded", "true");
}

// Same cap as settings_file.MAX_BYTES. A larger file is not a settings file.
const SETTINGS_FILE_MAX_BYTES = 262144;

let pendingUndo = null;
const deletingManual = new Set();

function toast(message, bad, action) {
  const node = $("toast");
  clearTimeout(toast.timer);
  node.classList.toggle("bad", Boolean(bad));
  node.replaceChildren(document.createTextNode(message));
  node.hidden = false;
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "toast-action";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      if (button.disabled) return;
      button.disabled = true;
      clearTimeout(toast.timer);
      node.hidden = true;
      action.run();
    });
    node.appendChild(button);
  } else {
    pendingUndo = null;
  }
  const snapshot = action && action.snapshot;
  toast.timer = setTimeout(() => {
    node.hidden = true;
    if (snapshot && pendingUndo === snapshot) pendingUndo = null;
  }, action ? 8000 : 4200);
}

// Set once Quit has stopped the collector. Later calls wait forever instead of failing.
let appStopped = false;

async function api(path, options) {
  if (appStopped) return new Promise(() => {});
  const request = options || {};
  const headers = { Accept: "application/json" };
  if (request.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method: request.method || "GET",
    headers,
    body: request.body,
  });
  if (response.status === 204) return null;
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (_error) {
      payload = { error: text };
    }
  }
  if (!response.ok) {
    const error = new Error((payload && payload.error) || response.statusText || "Request failed");
    if (payload && payload.duplicate === true) {
      error.duplicate = true;
      error.authorName = payload.author_name || "";
      error.amount = payload.amount;
      error.currency = payload.currency || "";
    }
    throw error;
  }
  return payload;
}

function queryString() {
  const params = new URLSearchParams();
  const filters = state.filters;
  if (filters.platform) params.set("platform", filters.platform);
  if (filters.q) params.set("q", filters.q);
  if (filters.hideReceipts) params.append("exclude_kind", "gift_membership_received");
  params.set("limit", String(filters.limit));
  params.set("offset", String(filters.offset));
  return params.toString();
}

function formatWhen(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function localParts(iso) {
  if (!iso) return { date: "—", time: "—" };
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return { date: iso, time: "—" };
  return {
    date: `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`,
    time: `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`,
  };
}

function donorName(name) {
  const text = String(name || "").trim().replace(/^@+/, "");
  return text || "—";
}

function formatPlain(value) {
  if (value == null || value === "" || !Number.isFinite(Number(value))) return "—";
  return Number(value).toFixed(2);
}

function formatRunning(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0.00";
  return number.toFixed(2);
}

function isKickCoins(row) {
  return row.kind === "kicks" || String(row.currency || "").toUpperCase() === "KICKS";
}

function ledgerAmount(row) {
  // Kick stores a coin count. Membership gifts store no amount at all.
  // Both are priced later, and that price is what the ledger shows.
  if (row.quoted_amount != null) return formatPlain(row.quoted_amount);
  if (isKickCoins(row)) return formatPlain(row.amount_usd);
  return formatPlain(row.amount_value);
}

function ledgerCurrency(row) {
  if (row.quoted_amount != null) return row.quoted_currency || "—";
  if (isKickCoins(row)) return row.amount_usd == null ? "—" : "USD";
  return row.currency || "—";
}

function donoAmountBand(usd) {
  const amount = Number(usd);
  if (!(amount > 0)) return 0;
  if (amount >= 100) return 100;
  if (amount >= 50) return 50;
  if (amount >= 20) return 20;
  if (amount >= 10) return 10;
  if (amount >= 5) return 5;
  if (amount >= 2) return 2;
  return 1;
}

function highlightRemaining(row, now) {
  const stamp = Date.parse(row.received_at || "");
  if (!Number.isFinite(stamp)) return 0;
  const age = now - stamp;
  if (age < -5000 || age >= HIGHLIGHT_MS) return 0;
  return HIGHLIGHT_MS - Math.max(0, age);
}

function syncHighlight(row, now) {
  const left = highlightRemaining(row, now);
  const existing = highlightTimers.get(row.id);
  if (existing) {
    clearTimeout(existing);
    highlightTimers.delete(row.id);
  }
  if (left <= 0) return false;
  highlightTimers.set(row.id, setTimeout(() => {
    highlightTimers.delete(row.id);
    const node = document.querySelector(`#rows tr[data-id="${row.id}"]`);
    if (node) node.classList.remove("is-new");
  }, left));
  return true;
}

function formatUsd(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(Number(value));
}

function round2(value) {
  return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
}

function formatStreamUsd(value) {
  const number = round2(value);
  const sign = number < 0 ? "-" : "";
  const [whole, frac] = Math.abs(number).toFixed(2).split(".");
  return `${sign}$${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${frac}`;
}

function oneLine(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function parseAmount(text) {
  const value = String(text || "").trim();
  if (!value) return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return number;
}

function namedTiers(tiers) {
  return (tiers || [])
    .filter((tier) => tier.enabled && Number(tier.amount) > 0 && oneLine(tier.name))
    .slice()
    .sort((a, b) => Number(a.amount) - Number(b.amount));
}

function streamChatLine(total, report) {
  let body = `${formatStreamUsd(total)} total for stream.`;
  if (report && report.tiers_enabled) {
    const active = namedTiers(report.tiers);
    if (active.length) {
      const next = active.find((tier) => total < Number(tier.amount) - 0.01);
      const bonusOf = (tier) => (oneLine(tier.bonus) ? ` and ${oneLine(tier.bonus)}` : "");
      if (next) {
        body += ` ${formatStreamUsd(round2(Number(next.amount) - total))} from ${oneLine(next.name)}${bonusOf(next)}`;
      } else {
        const last = active[active.length - 1];
        body += ` ${formatStreamUsd(round2(total - Number(last.amount)))} over ${oneLine(last.name)}${bonusOf(last)}`;
      }
    }
  }
  const extra = oneLine(report && report.chat_extra);
  if (extra) body += ` ${extra}`;
  return body;
}

function readClipboard() {
  const desktop = window.desktop;
  if (desktop && typeof desktop.readClipboard === "function") {
    try {
      const text = desktop.readClipboard();
      if (text && typeof text.then === "function") return text;
      if (typeof text === "string") return Promise.resolve(text);
    } catch (_error) {
      // A plain browser has no desktop bridge.
    }
  }
  if (navigator.clipboard && navigator.clipboard.readText) {
    return navigator.clipboard.readText();
  }
  return Promise.reject(new Error("Could not read the clipboard."));
}

function applyPaste(parsed) {
  $("manual-donor").value = parsed.donor || "";
  if (parsed.platform) $("manual-platform").value = parsed.platform;
  syncManualPlatformMark();
  if (parsed.kind) $("manual-kind").value = parsed.kind;
  $("manual-message").value = parsed.message || "";
  $("manual-native").value = parsed.amount || "";
  selectCurrency(parsed.currency || DEFAULT_CURRENCY);
  if (parsed.amount_usd) {
    $("manual-amount").value = parsed.amount_usd;
    toast("Filled from the clipboard.");
  } else if (parsed.amount) {
    $("manual-amount").value = "";
    toast(`No dollar rate for ${parsed.amount} ${parsed.currency}. Amount and currency were kept.`, true);
  } else if (parsed.kind === "membership") {
    $("manual-amount").value = "";
    const count = Number(parsed.membership_count);
    const read = count > 0 ? `Read ${count} memberships. ` : "";
    toast(`${read}This computer has no membership price saved. Set it in Settings and Save.`, true);
  } else {
    $("manual-amount").value = "";
    toast("Filled from the clipboard. The amount was not in the paste.");
  }
  updateStreamPreview();
}

function topDonorCopyText() {
  const report = state.stream || {};
  return report.donor_bar_copy || ($("donor-bar") && $("donor-bar").value) || "";
}

function copyText(text, ok) {
  const write = navigator.clipboard && navigator.clipboard.writeText(text);
  if (!write) {
    toast("Could not copy.", true);
    return;
  }
  write.then(() => toast(ok)).catch(() => toast("Could not copy.", true));
}

function formatAmount(row) {
  const amount = row.quoted_amount != null ? row.quoted_amount : row.amount_value;
  const currency = row.quoted_amount != null ? row.quoted_currency : row.currency;
  if (amount == null) return "—";
  if (currency === "KICKS") return `${Math.round(amount)} Kicks`;
  if (currency) {
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency,
      }).format(amount);
    } catch (_error) {
      return `${amount} ${currency}`;
    }
  }
  return String(amount);
}

function renderCurrencies(currencies) {
  const select = $("manual-currency");
  if (!select || !Array.isArray(currencies) || !currencies.length) return;
  const current = select.value || DEFAULT_CURRENCY;
  select.replaceChildren();
  currencies.forEach((item) => {
    if (!item || !item.code) return;
    const option = document.createElement("option");
    option.value = item.code;
    option.textContent = item.name ? item.code + " — " + item.name : item.code;
    option.defaultSelected = item.code === DEFAULT_CURRENCY;
    select.appendChild(option);
  });
  selectCurrency(current);
}

function selectCurrency(code) {
  const select = $("manual-currency");
  if (!select) return;
  const wanted = String(code || DEFAULT_CURRENCY).trim().toUpperCase() || DEFAULT_CURRENCY;
  const known = Array.from(select.options).some((option) => option.value === wanted);
  select.value = known ? wanted : DEFAULT_CURRENCY;
}

function renderPlatformPills(platforms) {
  const host = $("platform-pills");
  host.replaceChildren();
  host.appendChild(platformPill("", "All"));
  platforms.forEach((item) => host.appendChild(platformPill(item.id, item.label)));
  markPlatformPill(state.filters.platform);
}

function platformPill(id, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "pill";
  button.dataset.platform = id;
  if (id) button.appendChild(platformLabel(id, label));
  else button.appendChild(document.createTextNode(label));
  button.addEventListener("click", () => {
    state.filters.platform = id;
    state.filters.offset = 0;
    markPlatformPill(id);
    refresh().catch((error) => toast(error.message, true));
  });
  return button;
}

function markPlatformPill(id) {
  document.querySelectorAll("#platform-pills .pill").forEach((button) => {
    const selected = button.dataset.platform === id;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
  });
}

function renderSummary(summary) {
  const host = $("summary");
  host.replaceChildren();
  const cards = [
    ["Total donated", formatUsd(summary.total_usd)],
    ["Stored", String(summary.total)],
  ];
  cards.forEach(([label, figure]) => {
    const card = el("article");
    card.appendChild(el("p", "kicker", label));
    card.appendChild(el("p", "figure", figure));
    host.appendChild(card);
  });
}

function renderKioskBattery(payload) {
  const node = $("kiosk-battery");
  if (!node) return;
  const battery = payload && payload.battery;
  const percent = battery && Number(battery.percent);
  const show = Boolean(battery && battery.on_battery === true && Number.isFinite(percent));
  node.hidden = !show;
  if (!show) {
    delete node.dataset.level;
    return;
  }
  const clamped = Math.max(0, Math.min(100, Math.round(percent)));
  const label = $("kiosk-battery-percent");
  if (label) label.textContent = clamped + "%";
  if (clamped <= 20) node.dataset.level = "low";
  else delete node.dataset.level;
  const fill = node.querySelector(".kiosk-battery-fill");
  if (fill) fill.setAttribute("width", String(Math.max(1, Math.round((clamped / 100) * 14))));
  const caption = "Battery " + clamped + "%";
  node.setAttribute("aria-label", caption);
  node.title = caption;
}

function renderStatus(payload) {
  ["youtube", "rumble", "kick"].forEach((name) => {
    const item = (payload.platforms || {})[name] || {
      state: "unconfigured",
      detail: "Not started",
      error: "",
    };
    const node = $(name + "-status");
    node.textContent = item.error || item.detail || item.state;
    node.dataset.state = item.state || "";
  });
  const lines = (payload.log || []).map((row) => `${row.at}  ${row.platform}  ${row.message}`);
  $("log").textContent = lines.join("\n");
  const verbose = $("verbose-logs");
  if (verbose && !verbose.dataset.pending) verbose.checked = Boolean(payload.verbose);
  const logFile = $("log-file");
  if (logFile && payload.log_file) logFile.textContent = payload.log_file;
  renderKioskBattery(payload);
}

function renderRows(page) {
  const reported = Number(page.total);
  state.total = Number.isFinite(reported) ? reported : 0;
  const limit = state.filters.limit;
  const lastOffset = state.total === 0 ? 0 : Math.floor((state.total - 1) / limit) * limit;
  if (state.filters.offset > lastOffset) {
    state.filters.offset = lastOffset;
    return false;
  }
  state.rows = page.donations || [];
  const now = Date.now();
  const body = $("rows");
  body.replaceChildren();
  state.rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.dataset.id = String(row.id);
    tr.dataset.platform = row.platform || "other";
    tr.dataset.donoBand = String(donoAmountBand(row.amount_usd));
    if (syncHighlight(row, now)) tr.classList.add("is-new");
    if (state.selected && state.selected.id === row.id) tr.classList.add("is-selected");
    if (editingId != null && row.id === editingId) tr.classList.add("is-editing");
    tr.tabIndex = 0;
    if (row.hidden) tr.classList.add("is-hidden");
    tr.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      openDonation(row.id);
    });
    tr.addEventListener("keydown", (event) => {
      if (event.target !== tr) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDonation(row.id);
      }
    });
    const when = localParts(row.created_at);
    const dateCell = el("td", "num", when.date);
    const timeCell = el("td", "num", when.time);
    dateCell.title = row.created_at || "";
    timeCell.title = row.created_at || "";
    const donor = donorName(row.author_name);
    const donorCell = el("td", "donor-cell", donor);
    if (donor !== "—") donorCell.title = donor;
    const message = el("td", "message-cell", row.message || "—");
    if (row.message) message.title = row.message;
    tr.append(
      dateCell,
      timeCell,
      donorCell,
      el("td", "num", ledgerAmount(row)),
      platformCell(row),
      el("td", null, row.kind_label || row.kind),
      el("td", null, ledgerCurrency(row)),
      el("td", "num", formatPlain(row.amount_usd)),
      message,
      el("td", "num", row.hidden ? "—" : formatRunning(row.running_usd)),
      rowAction(row),
    );
    body.appendChild(tr);
  });
  $("empty").hidden = state.rows.length !== 0;
  const pages = state.total === 0 ? 1 : Math.ceil(state.total / limit);
  const pageNum = state.total === 0 ? 1 : Math.floor(state.filters.offset / limit) + 1;
  $("page-label").textContent = `Page ${pageNum} of ${pages}`;
  $("prev").disabled = pageNum <= 1;
  $("next").disabled = pageNum >= pages;
  const exportQuery = queryString()
    .replace(/(^|&)limit=\d+&?/, "$1")
    .replace(/(^|&)offset=\d+&?/, "$1")
    .replace(/&$/, "")
    .replace(/^&/, "");
  $("export-csv").href = "/api/export.csv?" + exportQuery;
  $("export-jsonl").href = "/api/export.jsonl?" + exportQuery;
  renderUtilityRows();
  return true;
}

function renderArchive(view) {
  const groups = (view && view.groups) || [];
  const ledgers = archiveLedgers(groups);
  const summary = $("archive-summary");
  if (summary) {
    if (!ledgers.length) summary.textContent = "Nothing is archived yet.";
    else {
      const noun = ledgers.length === 1 ? "saved ledger" : "saved ledgers";
      summary.textContent = `${ledgers.length} ${noun}.`;
    }
  }
  const empty = $("archive-empty");
  if (empty) empty.hidden = ledgers.length !== 0;
  const list = $("archive-list");
  if (!list) return;
  list.replaceChildren();
  ledgers.forEach((ledger, index) => {
    list.appendChild(archiveLedgerSection(ledger, index));
  });
}

function archiveLedgers(groups) {
  const ledgers = [];
  const byId = new Map();
  groups.forEach((group) => {
    const id = String(group.stream_id || "");
    let ledger = byId.get(id);
    if (!ledger) {
      ledger = { stream_id: id, title: group.title || "Stream", dates: [] };
      byId.set(id, ledger);
      ledgers.push(ledger);
    }
    ledger.dates.push(group);
  });
  return ledgers;
}

function archiveLedgerSection(ledger, index) {
  const dates = ledger.dates.filter((group) => archiveCount(group) > 0);
  const section = document.createElement("section");
  section.className = "archive-ledger";
  const title = ledger.title || "Stream";
  const many = dates.length > 1;
  const head = archiveToggle(dates.length > 0);
  const name = el("span", "archive-day-name", title);
  name.title = title;
  head.appendChild(name);
  if (!many && dates.length === 1 && dates[0].date) {
    head.appendChild(el("span", "archive-day-date", dates[0].date));
  }
  head.appendChild(el("span", "archive-day-meta", archiveDayMeta(
    dates.length
      ? { count: archiveLedgerCount(dates), total_usd: archiveLedgerTotal(dates), entries: [] }
      : { count: 0, total_usd: 0, entries: [] },
  )));
  section.appendChild(head);
  if (!dates.length) return section;
  const body = document.createElement("div");
  body.id = "archive-ledger-" + index;
  body.hidden = true;
  head.setAttribute("aria-controls", body.id);
  if (many) {
    body.className = "archive-ledger-body";
    head.setAttribute("aria-label", archiveLedgerLabel(title, false));
    dates.forEach((group, dateIndex) => {
      body.appendChild(archiveDateSection(group, title, index + "-" + dateIndex));
    });
    bindArchiveToggle(head, body, (open) => archiveLedgerLabel(title, open));
  } else {
    const group = dates[0];
    body.className = "archive-day-body";
    body.appendChild(archiveEntryTable(group.entries || []));
    head.setAttribute("aria-label", archiveDayLabel(group, false));
    bindArchiveToggle(head, body, (open) => archiveDayLabel(group, open));
  }
  section.appendChild(body);
  return section;
}

function archiveDateSection(group, title, id) {
  const section = document.createElement("section");
  section.className = "archive-day";
  const head = archiveToggle(true);
  const when = el("span", "archive-day-name", group.date || "—");
  head.append(when, el("span", "archive-day-meta", archiveDayMeta(group)));
  const body = document.createElement("div");
  body.className = "archive-day-body";
  body.id = "archive-day-" + id;
  body.hidden = true;
  body.appendChild(archiveEntryTable(group.entries || []));
  head.setAttribute("aria-controls", body.id);
  const labeled = { title: title, date: group.date };
  head.setAttribute("aria-label", archiveDayLabel(labeled, false));
  bindArchiveToggle(head, body, (open) => archiveDayLabel(labeled, open));
  section.append(head, body);
  return section;
}

function archiveToggle(expandable) {
  const head = document.createElement(expandable ? "button" : "div");
  head.className = expandable ? "archive-day-toggle" : "archive-day-head";
  if (expandable) {
    head.type = "button";
    head.setAttribute("aria-expanded", "false");
    head.appendChild(icon("chevron"));
  }
  return head;
}

function bindArchiveToggle(head, body, labelFor) {
  head.addEventListener("click", () => {
    const open = head.getAttribute("aria-expanded") === "true";
    head.setAttribute("aria-expanded", open ? "false" : "true");
    head.setAttribute("aria-label", labelFor(!open));
    body.hidden = open;
  });
}

function archiveLedgerLabel(title, open) {
  return (open ? "Hide dates for " : "Show dates for ") + title;
}

function archiveCount(group) {
  const count = Number(group.count);
  if (Number.isFinite(count)) return count;
  return Array.isArray(group.entries) ? group.entries.length : 0;
}

function archiveLedgerCount(dates) {
  return dates.reduce((sum, group) => sum + archiveCount(group), 0);
}

function archiveLedgerTotal(dates) {
  return dates.reduce((sum, group) => {
    const usd = Number(group.total_usd);
    return Number.isFinite(usd) ? round2(sum + usd) : sum;
  }, 0);
}

function archiveDayLabel(group, open) {
  const name = group.title || "Stream";
  const when = group.date ? " on " + group.date : "";
  return (open ? "Hide donations for " : "Show donations for ") + name + when;
}

function archiveDayMeta(group) {
  const count = Number(group.count);
  const shown = Number.isFinite(count) ? count : (group.entries || []).length;
  if (!shown) return "No donations";
  const noun = shown === 1 ? "donation" : "donations";
  const total = Number(group.total_usd);
  return Number.isFinite(total) ? `${shown} ${noun} · ${formatUsd(total)}` : `${shown} ${noun}`;
}

function archiveAmount(row) {
  const amount = ledgerAmount(row);
  const currency = ledgerCurrency(row);
  if (amount === "—") return "—";
  if (!currency || currency === "—") return amount;
  return `${amount} ${currency}`;
}

function archiveEntryTable(entries) {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  const headRow = document.createElement("tr");
  ["Time", "Donor", "Amount", "Platform", "Type", "USD", "Message"].forEach((label) => {
    headRow.appendChild(el("th", null, label));
  });
  const thead = document.createElement("thead");
  thead.appendChild(headRow);
  const tbody = document.createElement("tbody");
  entries.forEach((row) => {
    const tr = document.createElement("tr");
    tr.dataset.platform = row.platform || "other";
    tr.dataset.donoBand = String(donoAmountBand(row.amount_usd));
    if (row.hidden) tr.classList.add("is-hidden");
    const when = localParts(row.created_at);
    const timeCell = el("td", "num", when.time);
    timeCell.title = row.created_at || "";
    const donor = donorName(row.author_name);
    const donorCell = el("td", "donor-cell", donor);
    if (donor !== "—") donorCell.title = donor;
    const message = el("td", "message-cell", row.message || "—");
    if (row.message) message.title = row.message;
    tr.append(
      timeCell,
      donorCell,
      el("td", "num", archiveAmount(row)),
      platformCell(row),
      el("td", null, row.kind_label || row.kind || "—"),
      el("td", "num", row.hidden ? "—" : formatPlain(row.amount_usd)),
      message,
    );
    tbody.appendChild(tr);
  });
  table.append(thead, tbody);
  wrap.appendChild(table);
  return wrap;
}

function rowAction(row) {
  const cell = el("td", "row-actions");
  if (row.manual) {
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "icon-button row-action";
    edit.setAttribute("aria-label", "Edit");
    edit.title = "Edit";
    edit.appendChild(icon("pencil"));
    edit.addEventListener("click", (event) => {
      event.stopPropagation();
      beginEdit(row);
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button row-action is-delete";
    remove.setAttribute("aria-label", "Delete");
    remove.title = "Delete this manual entry";
    remove.appendChild(icon("trash"));
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteManual(row);
    });
    cell.append(edit, remove);
  } else {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "icon-button row-action";
    const hidden = Boolean(row.hidden);
    button.setAttribute("aria-label", hidden ? "Count again" : "Hide");
    button.title = hidden
      ? "Count this entry again"
      : "Hide this entry. It stays stored and is left out of totals and exports.";
    button.appendChild(icon(hidden ? "eye" : "eye-off"));
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      setEntryHidden(row, !hidden);
    });
    cell.appendChild(button);
  }
  return cell;
}

function manualSnapshot(row) {
  return {
    id: row.id,
    dedupe_key: row.dedupe_key,
    platform: row.platform,
    kind: row.kind,
    author_name: row.author_name,
    message: row.message || "",
    amount: row.amount_value,
    currency: row.currency,
    created_at: row.created_at,
    received_at: row.received_at,
    processed: Boolean(row.processed),
    processed_at: row.processed_at,
    note: row.note || "",
  };
}

function undoManual(snapshot) {
  if (pendingUndo !== snapshot) return;
  pendingUndo = null;
  api("/api/donations/restore", { method: "POST", body: JSON.stringify(snapshot) })
    .then(() => {
      toast("Restored that manual entry.");
      return refresh();
    })
    .catch((error) => toast(error.message, true));
}

function deleteManual(row) {
  if (deletingManual.has(row.id)) return;
  const snapshot = manualSnapshot(row);
  deletingManual.add(row.id);
  api("/api/donations/" + row.id, { method: "DELETE" })
    .then(() => {
      if (state.selected && state.selected.id === row.id) {
        $("drawer").hidden = true;
        state.selected = null;
      }
      if (editingId === row.id) cancelManualEdit();
      pendingUndo = snapshot;
      toast("Deleted that manual entry.", false, {
        label: "Undo",
        snapshot,
        run: () => undoManual(snapshot),
      });
      return refresh().catch(() => {});
    })
    .catch((error) => toast(error.message, true))
    .finally(() => deletingManual.delete(row.id));
}

function setEntryHidden(row, hidden) {
  api("/api/donations/" + row.id, {
    method: "PATCH",
    body: JSON.stringify({ hidden: hidden }),
  })
    .then(() => {
      toast(hidden ? "Hidden. Totals and exports leave it out." : "That entry counts again.");
      return refresh();
    })
    .catch((error) => toast(error.message, true));
}

function syncManualPlatformMark() {
  const select = $("manual-platform");
  const mark = $("manual-platform-mark");
  const label = $("manual-platform-label");
  if (!select) return;
  const id = platformKey(select.value) || "other";
  if (label) label.dataset.platform = id;
  if (!mark) return;
  const icon = PLATFORM_ICONS[id];
  mark.hidden = !icon;
  if (icon) mark.src = icon;
}

function platformCell(row) {
  const cell = document.createElement("td");
  cell.appendChild(platformLabel(row.platform, row.platform_label || row.platform));
  return cell;
}

function fact(dl, label, value) {
  dl.appendChild(el("dt", null, label));
  const dd = document.createElement("dd");
  if (value instanceof Node) dd.appendChild(value);
  else dd.textContent = value || "—";
  dl.appendChild(dd);
}

function renderDrawer(donation) {
  state.selected = donation;
  $("drawer").hidden = false;
  $("drawer-title").textContent = donation.kind_label || "Donation";
  const facts = $("drawer-facts");
  facts.replaceChildren();
  fact(facts, "When", donation.created_at);
  fact(facts, "Platform", platformLabel(donation.platform, donation.platform_label));
  fact(facts, "Type", donation.kind_label);
  fact(facts, "From", donorName(donation.author_name));
  fact(facts, "Channel", donation.channel);
  fact(facts, "Amount", formatAmount(donation));
  if (donation.amount_usd != null && donation.quoted_currency && donation.quoted_currency !== "USD") {
    fact(facts, "Amount USD", formatUsd(donation.amount_usd));
  }
  fact(facts, "External id", donation.external_id);
  $("drawer-message").textContent = donation.message || "";
  if (document.activeElement !== $("note")) {
    $("note").value = donation.note || "";
  }
  $("raw").textContent = JSON.stringify(donation.raw, null, 2);
}

async function openDonation(id) {
  const donation = await api("/api/donations/" + id);
  renderDrawer(donation);
  document.querySelectorAll("#rows tr").forEach((row) => {
    row.classList.toggle("is-selected", row.dataset.id === String(id));
  });
}

function cancelManualEdit() {
  if (editingId == null) return;
  const form = $("manual-entry");
  if (form) form.reset();
  setManualEditing(null);
}

const CHAT_TARGETS_KEY = "stream-donations-chat-targets";
const RUMBLE_CHAT_KEY = "stream-donations-rumble-chat";
const CHAT_TARGET_ORDER = ["youtube", "rumble", "kick"];
const RUMBLE_CHAT_BLOCK = "Continue on the Rumble page before chat can be posted there.";
let chatSendBusy = false;
let chatDestinationsLoaded = false;
let chatDestinations = { youtube: {}, kick: {} };
let rumbleAckBound = false;

function rumbleChatAllowed() {
  try {
    return localStorage.getItem(RUMBLE_CHAT_KEY) === "1";
  } catch (_error) {
    return false;
  }
}

function setRumbleChatAllowed(on) {
  try {
    if (on) localStorage.setItem(RUMBLE_CHAT_KEY, "1");
    else localStorage.removeItem(RUMBLE_CHAT_KEY);
  } catch (_error) {
    // The choice stays off when this browser store is blocked.
  }
  applyRumbleChatAck();
  applyChatTargets();
  renderCommandNotes();
}

function applyRumbleChatAck() {
  const on = rumbleChatAllowed();
  const button = $("rumble-chat-continue");
  const note = $("rumble-chat-ack");
  if (button) {
    button.textContent = on ? "Turn off" : "Continue";
    if (!rumbleAckBound) {
      rumbleAckBound = true;
      button.addEventListener("click", () => setRumbleChatAllowed(!rumbleChatAllowed()));
    }
  }
  if (note) note.hidden = !on;
}

function readChatTargets() {
  let stored = "";
  try {
    stored = sessionStorage.getItem(CHAT_TARGETS_KEY) || "";
  } catch (_error) {
    stored = "";
  }
  if (!stored) return { youtube: true, kick: true, rumble: true };
  try {
    const parsed = JSON.parse(stored);
    return {
      youtube: !parsed || parsed.youtube !== false,
      kick: !parsed || parsed.kick !== false,
      rumble: !parsed || parsed.rumble !== false,
    };
  } catch (_error) {
    return { youtube: true, kick: true, rumble: true };
  }
}

function writeChatTargets(targets) {
  try {
    sessionStorage.setItem(CHAT_TARGETS_KEY, JSON.stringify({
      youtube: Boolean(targets.youtube),
      kick: Boolean(targets.kick),
      rumble: Boolean(targets.rumble),
    }));
  } catch (_error) {
    // The choice is for this tab. A blocked session store still sends the buttons on screen.
  }
}

function applyChatTargets() {
  applyRumbleChatAck();
  const targets = readChatTargets();
  const notes = [];
  CHAT_TARGET_ORDER.forEach((platform) => {
    const button = $("chat-target-" + platform);
    if (!button) return;
    if (platform === "rumble") {
      const allowed = rumbleChatAllowed();
      button.disabled = !allowed;
      button.setAttribute("aria-pressed", allowed && targets.rumble ? "true" : "false");
      button.title = allowed ? "Rumble" : RUMBLE_CHAT_BLOCK;
      if (!allowed) notes.push(RUMBLE_CHAT_BLOCK);
      const span = button.querySelector("span");
      if (span) span.textContent = "Rumble";
      return;
    }
    const info = chatDestinations[platform] || {};
    const fallback = platform === "youtube" ? "YouTube" : "Kick";
    const span = button.querySelector("span");
    if (span) span.textContent = info.name || fallback;
    button.setAttribute("aria-pressed", targets[platform] ? "true" : "false");
    const blocked = chatDestinationsLoaded && !info.can_send;
    button.disabled = blocked;
    if (blocked && info.reason) {
      button.title = info.reason;
      notes.push(info.reason);
    } else {
      button.title = info.name || fallback;
    }
  });
  const note = $("chat-target-note");
  if (note) note.textContent = notes.join(" ");
}

function loadChatDestinations() {
  return api("/api/chat/destinations").then((payload) => {
    chatDestinations = {
      youtube: (payload && payload.youtube) || {},
      kick: (payload && payload.kick) || {},
    };
    chatDestinationsLoaded = true;
    applyChatTargets();
    renderCommandNotes();
  }).catch(() => {});
}

// Chat commands. The collector reads !total and !top from public chat and
// answers YouTube and Kick itself. Rumble replies are typed by this window.
const COMMAND_POLL_MS = 3000;
let commandDrainBusy = false;

function rumbleCommandsReady() {
  const desktop = window.desktop;
  return Boolean(desktop && typeof desktop.rumbleSend === "function" && rumbleChatAllowed());
}

function commandReadNote(platform, settings) {
  if (platform === "kick") {
    return settings.kick_watch ? "" : "Commands are read from the public channel above. Add one first.";
  }
  const follow = Boolean(settings[platform + "_follow_live"]);
  const target = follow ? settings[platform + "_channel"] : settings[platform + "_watch"];
  if (target) return "";
  if (platform === "youtube") {
    return "Commands are read from a public video URL or a followed channel. Add one first. The Google sign-in alone does not read ordinary chat.";
  }
  return "Commands are read from a public video URL or a followed channel. Add one first.";
}

function commandSendNote(platform) {
  if (platform === "rumble") {
    const desktop = window.desktop;
    if (!desktop || typeof desktop.rumbleSend !== "function") {
      return "This chat can be read but not answered here. Rumble replies need the SuperBerry desktop app.";
    }
    if (!rumbleChatAllowed()) return "This chat can be read but not answered until Continue is used above.";
    return "";
  }
  if (!chatDestinationsLoaded) return "";
  const destination = chatDestinations[platform] || {};
  if (destination.can_send) return "";
  return "This chat can be read but not answered. " + (destination.reason || "Connect an account that can post.");
}

function renderCommandNotes() {
  const settings = state.settings || {};
  ["youtube", "rumble", "kick"].forEach((platform) => {
    const box = $(platform + "-chat-commands");
    const note = $(platform + "-commands-note");
    if (!box || !note) return;
    const text = box.checked ? (commandReadNote(platform, settings) || commandSendNote(platform)) : "";
    note.textContent = text;
    note.hidden = !text;
  });
}

function drainCommandReplies() {
  // Browser mode never takes Rumble replies, so the collector skips them.
  if (!window.desktop || commandDrainBusy) return;
  commandDrainBusy = true;
  const ready = rumbleCommandsReady();
  api("/api/commands/rumble?ready=" + (ready ? "1" : "0"))
    .then((payload) => {
      let chain = Promise.resolve();
      ((payload && payload.pending) || []).forEach((item) => {
        if (!item || !item.text) return;
        chain = chain
          .then(() => postRumbleChat(item.text, item.stream_id))
          .then((row) => {
            if (row && row.ok) return null;
            return api("/api/commands/rumble", {
              method: "POST",
              body: JSON.stringify({ id: item.id, ok: false, reason: (row && row.error) || "Rumble chat was not sent." }),
            });
          })
          .catch(() => {});
      });
      return chain;
    })
    .catch(() => {})
    .finally(() => {
      commandDrainBusy = false;
    });
}

function selectedChatPlatforms() {
  const names = [];
  CHAT_TARGET_ORDER.forEach((platform) => {
    const button = $("chat-target-" + platform);
    if (!button || button.disabled) return;
    if (button.getAttribute("aria-pressed") === "true") names.push(platform);
  });
  return names;
}

function chatPlatformName(id) {
  if (id === "youtube") return "YouTube";
  if (id === "rumble") return "Rumble";
  if (id === "kick") return "Kick";
  return id;
}

function chatSentLabel(names) {
  if (names.length <= 1) return names[0] || "";
  if (names.length === 2) return names[0] + " and " + names[1];
  return names.slice(0, -1).join(", ") + ", and " + names[names.length - 1];
}

function rumbleSendError(result) {
  if (result === "signed-out") return "Sign in to Rumble chat first.";
  if (result === "too-long") return "That message is too long for Rumble chat.";
  if (result === "no-composer") return "Rumble chat is not ready.";
  if (result === "empty") return "Type a message.";
  if (result === "overlong") return "That message is too long.";
  return "That Rumble chat could not be opened.";
}

function postRumbleChat(message, streamId) {
  if (!rumbleChatAllowed()) {
    return Promise.resolve({ platform: "rumble", ok: false, error: RUMBLE_CHAT_BLOCK });
  }
  const desktop = window.desktop;
  if (!desktop || typeof desktop.rumbleSend !== "function") {
    return Promise.resolve({
      platform: "rumble",
      ok: false,
      error: desktop ? "Open SuperBerry to use the Rumble window." : RUMBLE_SEND_NEEDS_DESKTOP,
    });
  }
  const rumblePath = streamId
    ? "/api/chat/rumble?stream=" + encodeURIComponent(streamId)
    : "/api/chat/rumble";
  return api(rumblePath).then((payload) => {
    const url = payload && payload.url;
    if (!url) throw new Error("That Rumble chat could not be opened.");
    return desktop.rumbleSend(message, url);
  }).then((result) => {
    if (result === "sent") return { platform: "rumble", ok: true };
    return { platform: "rumble", ok: false, error: rumbleSendError(result) };
  }).catch((error) => ({
    platform: "rumble",
    ok: false,
    error: (error && error.message) || "Rumble chat was not sent.",
  }));
}

function sendChatText(text, options) {
  const fromComposer = Boolean(options && options.fromComposer);
  const composer = (options && options.composer) || $("chat-compose");
  const platforms = selectedChatPlatforms();
  if (!platforms.length) {
    const note = $("chat-target-note");
    toast((note && note.textContent) || "Choose a chat.", true);
    return Promise.resolve();
  }
  const message = oneLine(text);
  if (!message) {
    toast("Type a message.", true);
    return Promise.resolve();
  }
  if (message.length > 2000) {
    toast("That message is too long.", true);
    return Promise.resolve();
  }
  if (chatSendBusy) return Promise.resolve();
  chatSendBusy = true;
  const sendButtons = ["chat-send", "kiosk-chat-send"].map($).filter(Boolean);
  sendButtons.forEach((button) => {
    button.disabled = true;
  });
  const apiPlatforms = platforms.filter((name) => name !== "rumble");
  const jobs = [];
  if (apiPlatforms.length) {
    jobs.push(api("/api/chat/send", {
      method: "POST",
      body: JSON.stringify({ text: message, platforms: apiPlatforms }),
    }).then((payload) => (payload && payload.results) || []).catch((error) => (
      apiPlatforms.map((platform) => ({
        platform,
        ok: false,
        error: (error && error.message) || "Chat was not sent.",
      }))
    )));
  }
  if (platforms.indexOf("rumble") !== -1) jobs.push(postRumbleChat(message).then((row) => [row]));
  return Promise.all(jobs).then((groups) => {
    const order = { youtube: 0, rumble: 1, kick: 2 };
    const results = groups.reduce((all, group) => all.concat(group), []);
    results.sort((left, right) => (order[left.platform] ?? 9) - (order[right.platform] ?? 9));
    const sent = results.filter((row) => row.ok).map((row) => chatPlatformName(row.platform));
    const failed = results.filter((row) => !row.ok);
    if (sent.length && !failed.length) {
      toast("Sent to " + chatSentLabel(sent) + ".");
      if (fromComposer && composer) composer.value = "";
      return;
    }
    const problem = failed.map((row) => row.error || "Chat was not sent.").join(" ");
    if (sent.length) toast("Sent to " + chatSentLabel(sent) + ". " + problem, true);
    else toast(problem || "Chat was not sent.", true);
  }).finally(() => {
    chatSendBusy = false;
    sendButtons.forEach((button) => {
      button.disabled = false;
    });
  });
}

// Without the desktop bridge, SuperBerry is open in a normal web browser.
function browserMode() {
  return !window.desktop;
}

const SITE_SIGN_IN = {
  youtube: { label: "YouTube", url: "https://www.youtube.com/signin" },
  kick: { label: "Kick", url: "https://kick.com/login" },
  rumble: { label: "Rumble", url: "https://rumble.com/login.php" },
};
const EMBEDDED_CHAT_NEEDS_DESKTOP = "This chat is embedded only in the SuperBerry desktop app. Use the pop-out button to open it in a new window.";
const RUMBLE_SEND_NEEDS_DESKTOP = "Sending to Rumble chat needs the SuperBerry desktop app.";

function openSiteInBrowser(platform) {
  const site = SITE_SIGN_IN[platform];
  if (!site) return;
  // With noopener the browser returns null even when the tab opened, so there is nothing to check.
  window.open(site.url, "_blank", "noopener");
  toast(site.label + " sign-in opened in a new tab. The browser keeps that session.");
}

// The window opens during the click, so a pop-up blocker allows it. The address arrives after.
function openChatInBrowser(platform, path, failure) {
  const popup = window.open("about:blank", "superberry-chat-" + platform, "popup,width=440,height=760");
  if (!popup) {
    toast("The browser blocked the chat window. Allow pop-ups for this page.", true);
    return;
  }
  try {
    popup.opener = null;
  } catch (_error) {
    // Some browsers do not let the opener be cleared. The chat page is still the platform's own.
  }
  api(path).then((payload) => {
    const url = payload && payload.url;
    if (!url || !/^https:\/\//.test(url)) throw new Error(failure);
    popup.location.replace(url);
  }).catch((error) => {
    try {
      popup.close();
    } catch (_error) {
      // Already closed.
    }
    toast((error && error.message) || failure, true);
  });
}

function requireRumbleDesktop() {
  const desktop = window.desktop;
  if (!desktop || typeof desktop.rumbleSignIn !== "function" || typeof desktop.rumbleChat !== "function") {
    toast("Open SuperBerry to use the Rumble window.", true);
    return null;
  }
  return desktop;
}

function openRumbleSignIn() {
  if (browserMode()) {
    openSiteInBrowser("rumble");
    return;
  }
  const desktop = requireRumbleDesktop();
  if (!desktop) return;
  Promise.resolve(desktop.rumbleSignIn()).catch((error) => {
    toast((error && error.message) || "Rumble sign-in could not open.", true);
  });
}

function openRumbleChat() {
  if (browserMode()) {
    openChatInBrowser("rumble", "/api/chat/rumble", "That Rumble chat could not be opened.");
    return;
  }
  const desktop = requireRumbleDesktop();
  if (!desktop) return;
  api("/api/chat/rumble").then((payload) => {
    const url = payload && payload.url;
    if (!url) throw new Error("That Rumble chat could not be opened.");
    return desktop.rumbleChat(url);
  }).then((result) => {
    if (result === "refused") toast("That Rumble chat could not be opened.", true);
  }).catch((error) => {
    toast((error && error.message) || "That Rumble chat could not be opened.", true);
  });
}

function requireYoutubeDesktop() {
  const desktop = window.desktop;
  if (!desktop || typeof desktop.youtubeSignIn !== "function" || typeof desktop.youtubeChat !== "function") {
    toast("Open SuperBerry to use the YouTube window.", true);
    return null;
  }
  return desktop;
}

function openYoutubeSignIn() {
  if (browserMode()) {
    openSiteInBrowser("youtube");
    return;
  }
  const desktop = requireYoutubeDesktop();
  if (!desktop) return;
  Promise.resolve(desktop.youtubeSignIn()).catch((error) => {
    toast((error && error.message) || "YouTube sign-in could not open.", true);
  });
}

function openYoutubeChat() {
  if (browserMode()) {
    openChatInBrowser("youtube", "/api/chat/youtube", "That YouTube chat could not be opened.");
    return;
  }
  const desktop = requireYoutubeDesktop();
  if (!desktop) return;
  api("/api/chat/youtube").then((payload) => {
    const url = payload && payload.url;
    if (!url) throw new Error("That YouTube chat could not be opened.");
    return desktop.youtubeChat(url);
  }).then((result) => {
    if (result === "refused") toast("That YouTube chat could not be opened.", true);
  }).catch((error) => {
    toast((error && error.message) || "That YouTube chat could not be opened.", true);
  });
}

function requireKickDesktop() {
  const desktop = window.desktop;
  if (!desktop || typeof desktop.kickSignIn !== "function" || typeof desktop.kickChat !== "function") {
    toast("Open SuperBerry to use the Kick window.", true);
    return null;
  }
  return desktop;
}

function openKickSignIn() {
  if (browserMode()) {
    openSiteInBrowser("kick");
    return;
  }
  const desktop = requireKickDesktop();
  if (!desktop) return;
  Promise.resolve(desktop.kickSignIn()).catch((error) => {
    toast((error && error.message) || "Kick sign-in could not open.", true);
  });
}

function openKickChat() {
  if (browserMode()) {
    openChatInBrowser("kick", "/api/chat/kick", "That Kick chat could not be opened.");
    return;
  }
  const desktop = requireKickDesktop();
  if (!desktop) return;
  api("/api/chat/kick").then((payload) => {
    const url = payload && payload.url;
    if (!url) throw new Error("That Kick chat could not be opened.");
    return desktop.kickChat(url);
  }).then((result) => {
    if (result === "refused") toast("That Kick chat could not be opened.", true);
  }).catch((error) => {
    toast((error && error.message) || "That Kick chat could not be opened.", true);
  });
}

const BLUE_CHAT_GAP_MIN_MS = 2000;
const BLUE_CHAT_GAP_MAX_MS = 6000;
const blueSeenIds = new Set();
const blueSeenOrder = [];
let blueChain = Promise.resolve();

function rememberBlue(id) {
  if (!id || blueSeenIds.has(id)) return false;
  blueSeenIds.add(id);
  blueSeenOrder.push(id);
  while (blueSeenOrder.length > 200) blueSeenIds.delete(blueSeenOrder.shift());
  return true;
}

function blueChatGap() {
  const span = BLUE_CHAT_GAP_MAX_MS - BLUE_CHAT_GAP_MIN_MS + 1;
  return BLUE_CHAT_GAP_MIN_MS + Math.floor(Math.random() * span);
}

function drainBlue() {
  blueChain = blueChain
    .then(() => api("/api/blue/pending"))
    .then((payload) => runBlueBatch((payload && payload.pending) || []))
    .catch(() => {});
}

function runBlueBatch(items) {
  let chain = Promise.resolve();
  let sentChat = false;
  (items || []).forEach((item) => {
    if (!item || !item.id || !rememberBlue(item.id)) return;
    const chat = item.kind === "chat";
    const wait = Boolean(chat && sentChat);
    if (chat) sentChat = true;
    chain = chain.then(() => {
      const pause = wait
        ? new Promise((resolve) => window.setTimeout(resolve, blueChatGap()))
        : Promise.resolve();
      return pause.then(() => performBlue(item)).catch(() => {});
    });
  });
  return chain;
}

function performBlue(item) {
  if (item.kind === "notice" && item.target === "app") {
    toast(item.text || "");
    return Promise.resolve();
  }
  if (item.kind === "chat") return sendBlueChat(item);
  return reportBlue(item, "That action could not be completed.");
}

function sendBlueChat(item) {
  if (item.target === "rumble") return sendBlueRumble(item);
  if (item.target !== "youtube" && item.target !== "kick") {
    return reportBlue(item, "That action could not be completed.");
  }
  return api("/api/chat/send", {
    method: "POST",
    body: JSON.stringify({
      text: item.text,
      platforms: [item.target],
      stream_id: item.stream_id || "",
    }),
  }).then((payload) => {
    const results = (payload && payload.results) || [];
    const row = results.find((entry) => entry.platform === item.target) || results[0];
    if (row && row.ok) return null;
    return reportBlue(item, (row && row.error) || "Chat was not sent.");
  }).catch((error) => reportBlue(item, (error && error.message) || "Chat was not sent."));
}

function sendBlueRumble(item) {
  if (!rumbleChatAllowed()) return reportBlue(item, RUMBLE_CHAT_BLOCK);
  const desktop = window.desktop;
  if (!desktop || typeof desktop.rumbleSend !== "function") {
    return reportBlue(item, desktop ? "Open SuperBerry to use the Rumble window." : RUMBLE_SEND_NEEDS_DESKTOP);
  }
  return postRumbleChat(item.text, item.stream_id).then((row) => {
    if (row && row.ok) return null;
    return reportBlue(item, (row && row.error) || "Rumble chat was not sent.");
  });
}

function reportBlue(item, reason) {
  return api("/api/blue/result", {
    method: "POST",
    body: JSON.stringify({
      ok: false,
      script: item.script || "",
      line: item.line,
      target: item.target || "",
      reason: reason || "That action could not be completed.",
    }),
  }).catch(() => {});
}

async function refresh(options) {
  const serial = ++refreshSerial;
  const [summary, status, page, stream] = await Promise.all([
    api("/api/summary"),
    api("/api/status"),
    api("/api/donations?" + queryString()),
    api("/api/stream"),
  ]);
  if (serial !== refreshSerial) return;
  const nextLedger = stream && stream.streams && stream.streams.active_id;
  const previousLedger = state.stream && state.stream.streams && state.stream.streams.active_id;
  if (previousLedger && nextLedger && previousLedger !== nextLedger) cancelManualEdit();
  renderSummary(summary);
  if (summary.fx) renderFx(summary.fx);
  renderStatus(status);
  const painted = renderRows(page);
  renderStream(stream);
  loadChatDestinations();
  drainBlue();
  if (!painted) {
    await refresh(options);
    return;
  }
  if (options && options.scroll) {
    const wrap = document.querySelector("#tab-ledger .table-wrap");
    if (wrap) wrap.scrollIntoView({ block: "start" });
  }
}

function enqueueStream(task) {
  const run = streamChain.then(task, task);
  streamChain = run.then(() => {}, () => {});
  return run;
}

const TAB_TOOLS_KEY = "stream-donations-tab-tools";

function tabToolsShown() {
  try {
    return localStorage.getItem(TAB_TOOLS_KEY) === "1";
  } catch (error) {
    return false;
  }
}

function setTabToolsShown(on) {
  try {
    if (on) localStorage.setItem(TAB_TOOLS_KEY, "1");
    else localStorage.removeItem(TAB_TOOLS_KEY);
  } catch (error) {
    return;
  }
}

function streamTabSignature(streams) {
  const tabs = (streams && streams.tabs) || [];
  const tools = tabToolsShown() ? "1" : "0";
  return tabs.map((tab) => tab.id + ":" + tab.title + ":" + (tab.paused ? "1" : "0")).join("|") + "#" + ((streams && streams.active_id) || "") + ":" + tools;
}

function revealStreamTab(tab) {
  const scroller = $("stream-tabs");
  if (!scroller || !tab) return;
  const tabBox = tab.getBoundingClientRect();
  const box = scroller.getBoundingClientRect();
  if (!box.width || !tabBox.width) return;
  if (tabBox.left < box.left) scroller.scrollLeft -= box.left - tabBox.left;
  else if (tabBox.right > box.right) scroller.scrollLeft += tabBox.right - box.right;
}

function updateStreamBumpers() {
  const scroller = $("stream-tabs");
  const prev = $("stream-tabs-prev");
  const next = $("stream-tabs-next");
  if (!scroller || !prev || !next) return;
  const overflow = scroller.scrollWidth - scroller.clientWidth > 2;
  prev.hidden = !overflow;
  next.hidden = !overflow;
  if (!overflow) return;
  prev.disabled = scroller.scrollLeft <= 1;
  next.disabled = scroller.scrollLeft + scroller.clientWidth >= scroller.scrollWidth - 1;
}

function scrollStreamTabs(direction) {
  const scroller = $("stream-tabs");
  if (!scroller) return;
  scroller.scrollBy({
    left: direction * Math.max(120, Math.round(scroller.clientWidth * 0.8)),
    behavior: "smooth",
  });
}

function armLedgerRename(id) {
  const now = Date.now();
  if (now - nameDialogRequestedAt < 700) return;
  nameDialogRequestedAt = now;
  openLedgerName(id).catch((error) => toast(error.message, true));
}

function renderStreamTabs(report) {
  const scroller = $("stream-tabs");
  if (!scroller) return;
  const streams = report.streams || { active_id: "", tabs: [] };
  const tabs = streams.tabs || [];
  const signature = streamTabSignature(streams);
  if (scroller.dataset.signature !== signature) {
    const returnFocus = scroller.contains(document.activeElement);
    scroller.dataset.signature = signature;
    scroller.replaceChildren();

    tabs.forEach((tab) => {
      const selected = tab.id === streams.active_id;
      const item = document.createElement("div");
      item.className = "stream-tab" + (selected ? " is-active" : "");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "stream-tab-label";
      button.setAttribute("role", "tab");
      button.id = "stream-tab-" + tab.id;
      button.setAttribute("aria-selected", selected ? "true" : "false");
      button.tabIndex = selected ? 0 : -1;
      button.title = (tab.title || "Stream") + ". Double-click to rename.";
      button.textContent = tab.title || "Stream";
      button.addEventListener("click", () => {
        const now = Date.now();
        const previous = lastTabClick;
        lastTabClick = { id: tab.id, at: now };
        if (previous && previous.id === tab.id && now - previous.at < 500) {
          armLedgerRename(tab.id);
          return;
        }
        switchStream(tab.id).catch((error) => toast(error.message, true));
      });
      button.addEventListener("dblclick", (event) => {
        event.preventDefault();
        armLedgerRename(tab.id);
      });
      item.appendChild(button);
      const paused = tab.paused !== false;
      const ledgerName = tab.title || "Stream";
      const addresses = document.createElement("button");
      addresses.type = "button";
      addresses.className = "stream-tab-addresses";
      addresses.setAttribute("aria-label", "Addresses for " + ledgerName);
      addresses.title = "Addresses for this ledger";
      addresses.appendChild(icon("link"));
      addresses.addEventListener("click", (event) => {
        event.stopPropagation();
        openLedgerAddresses(tab.id).catch((error) => toast(error.message, true));
      });
      const pause = document.createElement("button");
      pause.type = "button";
      pause.className = "stream-tab-pause";
      pause.setAttribute("aria-pressed", paused ? "true" : "false");
      pause.setAttribute("aria-label", paused ? "Reconnect" : "Soft disconnect");
      pause.title = paused
        ? "Reconnect. This ledger is paused. Sign-in and watch URLs were kept."
        : "Soft disconnect. Stop this ledger's API requests and live-chat reading without signing out.";
      pause.appendChild(icon(paused ? "unplug" : "plug"));
      pause.addEventListener("click", (event) => {
        event.stopPropagation();
        const next = pause.getAttribute("aria-pressed") !== "true";
        toggleLedgerPause(tab.id, next).catch((error) => toast(error.message, true));
      });
      if (tabToolsShown()) {
        item.appendChild(addresses);
        item.appendChild(pause);
      }
      const menuWrap = document.createElement("div");
      menuWrap.className = "menu-wrap";
      const menuButton = document.createElement("button");
      menuButton.type = "button";
      menuButton.className = "stream-tab-menu-button" + (paused && !tabToolsShown() ? " is-paused" : "");
      menuButton.setAttribute("aria-label", "Addresses and connect");
      menuButton.title = paused && !tabToolsShown()
        ? "Paused. Addresses and connect."
        : "Addresses and connect";
      menuButton.setAttribute("aria-haspopup", "menu");
      menuButton.setAttribute("aria-expanded", "false");
      menuButton.setAttribute("aria-controls", "stream-tab-menu-" + tab.id);
      menuButton.appendChild(icon("menu"));
      const menu = document.createElement("div");
      menu.id = "stream-tab-menu-" + tab.id;
      menu.className = "dropdown stream-tab-menu";
      menu.setAttribute("role", "menu");
      menu.setAttribute("aria-label", "Addresses and connect");
      menu.hidden = true;
      const addressItem = document.createElement("button");
      addressItem.type = "button";
      addressItem.className = "menu-item";
      addressItem.setAttribute("role", "menuitem");
      addressItem.appendChild(icon("link"));
      addressItem.appendChild(document.createTextNode("Addresses"));
      addressItem.addEventListener("click", (event) => {
        event.stopPropagation();
        closeMenu();
        openLedgerAddresses(tab.id).catch((error) => toast(error.message, true));
      });
      const connectItem = document.createElement("button");
      connectItem.type = "button";
      connectItem.className = "menu-item";
      connectItem.setAttribute("role", "menuitem");
      connectItem.appendChild(icon(paused ? "unplug" : "plug"));
      connectItem.appendChild(document.createTextNode(paused ? "Reconnect" : "Soft disconnect"));
      connectItem.addEventListener("click", (event) => {
        event.stopPropagation();
        closeMenu();
        toggleLedgerPause(tab.id, !paused).catch((error) => toast(error.message, true));
      });
      const always = document.createElement("button");
      always.type = "button";
      always.className = "menu-item";
      always.setAttribute("role", "menuitemcheckbox");
      always.setAttribute("aria-checked", tabToolsShown() ? "true" : "false");
      always.title = "Show addresses and the plug on every ledger tab.";
      if (tabToolsShown()) always.appendChild(icon("check"));
      always.appendChild(document.createTextNode("Always show"));
      always.addEventListener("click", (event) => {
        event.stopPropagation();
        setTabToolsShown(!tabToolsShown());
        closeMenu();
        renderStreamTabs(report);
      });
      const sep = document.createElement("div");
      sep.className = "menu-sep";
      sep.setAttribute("role", "separator");
      menu.append(addressItem, connectItem, sep, always);
      menuButton.addEventListener("click", (event) => {
        event.stopPropagation();
        const willOpen = menu.hidden;
        closeMenu();
        if (!willOpen) return;
        menuButton.setAttribute("aria-expanded", "true");
        placeStreamTabMenu(menuButton, menu);
      });
      menuWrap.append(menuButton, menu);
      item.appendChild(menuWrap);
      const close = document.createElement("button");
      close.type = "button";
      close.className = "stream-tab-close";
      close.setAttribute("aria-label", "Close " + (tab.title || "Stream"));
      close.appendChild(icon("x"));
      close.addEventListener("click", () => {
        closeStream(tab.id, tab.title).catch((error) => toast(error.message, true));
      });
      item.appendChild(close);
      scroller.appendChild(item);
    });
    const active = scroller.querySelector(".stream-tab.is-active");
    if (active) revealStreamTab(active);
    if (returnFocus) {
      const selected = scroller.querySelector("[aria-selected=true]");
      if (selected) selected.focus();
    }
  }
  updateStreamBumpers();
}

async function commitStreamTitle() {
  const input = $("stream-title");
  const dialog = $("ledger-name");
  // The field lives in the name dialog. Other tab actions call this to flush
  // an edit, and must not rename when that dialog is closed.
  if (!input || !dialog || !dialog.open) return;
  const next = input.value.trim();
  const current = (state.stream && state.stream.title) || "";
  if (next === current) return;
  const report = await api("/api/streams", {
    method: "POST",
    body: JSON.stringify({ action: "rename", title: next }),
  });
  renderStream(report);
}

async function reloadOpenStream() {
  applySettingsToForm(await api("/api/settings"));
  await refresh();
}

function toggleLedgerPause(id, paused) {
  return enqueueStream(async () => {
    await commitStreamTitle();
    const active = state.stream && state.stream.streams && state.stream.streams.active_id;
    const open = !active || active === id;
    await api("/api/collection", {
      method: "POST",
      body: JSON.stringify({ paused, id }),
    });
    if (paused && open) toast("Soft disconnect. This ledger is paused.");
    else if (!paused && open) toast("Reconnected. This ledger is collecting.");
    else if (paused) toast("That ledger is paused. It stays disconnected until you reconnect it.");
    else toast("That ledger is collecting, including while another ledger is open.");
    await reloadOpenStream();
  });
}

function switchStream(id) {
  if (state.stream && state.stream.streams && state.stream.streams.active_id === id) {
    return Promise.resolve();
  }
  return enqueueStream(async () => {
    await commitStreamTitle();
    await api("/api/streams", { method: "POST", body: JSON.stringify({ action: "switch", id }) });
    await reloadOpenStream();
  });
}

function addStream() {
  return enqueueStream(async () => {
    await commitStreamTitle();
    await api("/api/streams", { method: "POST", body: JSON.stringify({ action: "add" }) });
    await reloadOpenStream();
    await promptLedgerUrls();
  });
}

function openLedgerAddresses(id) {
  return enqueueStream(async () => {
    await commitStreamTitle();
    const active = state.stream && state.stream.streams && state.stream.streams.active_id;
    if (id && active && active !== id) {
      await api("/api/streams", { method: "POST", body: JSON.stringify({ action: "switch", id }) });
      await reloadOpenStream();
    }
    await promptLedgerUrls({ fresh: false });
  });
}

function openLedgerName(id) {
  return enqueueStream(async () => {
    await commitStreamTitle();
    const active = state.stream && state.stream.streams && state.stream.streams.active_id;
    if (id && active && active !== id) {
      await api("/api/streams", { method: "POST", body: JSON.stringify({ action: "switch", id }) });
      await reloadOpenStream();
    }
    await promptLedgerName();
  });
}

function promptLedgerName() {
  const dialog = $("ledger-name");
  const form = $("ledger-name-form");
  const cancel = $("ledger-name-cancel");
  const input = $("stream-title");
  if (!dialog || !form || !input || typeof dialog.showModal !== "function") {
    return Promise.resolve();
  }
  if (dialog.open) return Promise.resolve();
  const savedTitle = () => ((state.stream && state.stream.title) || "");
  input.value = savedTitle();
  const error = $("ledger-name-error");
  if (error) {
    error.hidden = true;
    error.textContent = "";
  }
  const streamRow = $("ledger-name-stream");
  if (streamRow) streamRow.hidden = true;
  const serial = ++ledgerNameSerial;
  loadLedgerStreamTitles(serial);
  return new Promise((resolve) => {
    let settled = false;
    const use = $("ledger-name-use");
    const detach = () => {
      form.removeEventListener("submit", onSubmit);
      dialog.removeEventListener("close", onClose);
      if (cancel) cancel.removeEventListener("click", onCancel);
      if (use) use.removeEventListener("click", onUse);
    };
    const finish = () => {
      if (settled) return;
      settled = true;
      detach();
      input.value = savedTitle();
      resolve();
    };
    const onClose = () => finish();
    const onCancel = () => {
      if (!settled) dialog.close();
    };
    const onUse = () => {
      if (!settled) useLedgerStreamTitle();
    };
    const onSubmit = (event) => {
      event.preventDefault();
      if (settled) return;
      const saving = form.querySelector("button.primary");
      if (saving) saving.disabled = true;
      if (error) {
        error.hidden = true;
        error.textContent = "";
      }
      commitStreamTitle()
        .then(() => {
          if (!settled) dialog.close();
        })
        .catch((err) => {
          if (error) {
            error.hidden = false;
            error.textContent = err.message || "Could not save that name.";
          } else toast(err.message || "Could not save that name.", true);
        })
        .finally(() => {
          if (saving) saving.disabled = false;
        });
    };
    form.addEventListener("submit", onSubmit);
    dialog.addEventListener("close", onClose);
    if (cancel) cancel.addEventListener("click", onCancel);
    if (use) use.addEventListener("click", onUse);
    dialog.showModal();
    input.focus();
    input.select();
  });
}

function loadLedgerStreamTitles(serial) {
  api("/api/stream-titles")
    .then((result) => {
      const dialog = $("ledger-name");
      if (serial !== ledgerNameSerial || !dialog || !dialog.open) return;
      paintLedgerStreamTitles(result);
    })
    .catch(() => {
      /* The name can still be typed. A missed lookup does not rename the ledger. */
    });
}

function paintLedgerStreamTitles(result) {
  const row = $("ledger-name-stream");
  const select = $("ledger-name-platform");
  const button = $("ledger-name-use");
  const streams = Array.isArray(result && result.streams) ? result.streams : [];
  const usable = streams.filter((item) => item && item.title && PLATFORM_NAMES[item.platform]);
  if (!row || !select || !button || !usable.length) {
    if (row) row.hidden = true;
    return;
  }
  select.replaceChildren();
  usable.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.platform;
    option.dataset.title = item.title;
    option.textContent = PLATFORM_NAMES[item.platform] + ": " + item.title;
    select.appendChild(option);
  });
  const single = usable.length === 1;
  const label = select.closest("label");
  select.hidden = single;
  if (label) label.hidden = single;
  const name = PLATFORM_NAMES[usable[0].platform];
  button.textContent = single ? "Use " + name + " title" : "Use stream title";
  button.title = single ? usable[0].title : "Use stream title";
  row.hidden = false;
}

function useLedgerStreamTitle() {
  const select = $("ledger-name-platform");
  const input = $("stream-title");
  if (!select || !input) return;
  const option = select.options[select.selectedIndex];
  const title = option && option.dataset.title;
  if (!title) return;
  input.value = title;
  input.focus();
  input.select();
}

function setSectionExpanded(button, expanded) {
  const body = document.getElementById(button.getAttribute("aria-controls") || "");
  const section = button.closest(".card");
  const name = button.getAttribute("data-section") || "section";
  const label = (expanded ? "Collapse " : "Expand ") + name;
  button.setAttribute("aria-expanded", expanded ? "true" : "false");
  button.setAttribute("aria-label", label);
  button.title = label;
  if (body) body.hidden = !expanded;
  if (section) section.classList.toggle("is-collapsed", !expanded);
}

function bindSectionToggles() {
  document.querySelectorAll(".section-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      setSectionExpanded(button, button.getAttribute("aria-expanded") !== "true");
    });
  });
}

const LEDGER_ADDRESS_FIELDS = [
  {
    followId: "ledger-youtube-follow",
    inputId: "ledger-youtube",
    labelId: "ledger-youtube-label",
    watchKey: "youtube_watch",
    channelKey: "youtube_channel",
    followKey: "youtube_follow_live",
    platform: "youtube",
  },
  {
    followId: "ledger-rumble-follow",
    inputId: "ledger-rumble",
    labelId: "ledger-rumble-label",
    watchKey: "rumble_watch",
    channelKey: "rumble_channel",
    followKey: "rumble_follow_live",
    platform: "rumble",
  },
];

function ledgerAddressMode(field) {
  const input = $(field.inputId);
  const label = $(field.labelId);
  const follow = $(field.followId);
  const on = Boolean(follow && follow.checked);
  if (input) {
    input.placeholder = on
      ? input.dataset.channelPlaceholder || ""
      : input.dataset.videoPlaceholder || "";
  }
  if (label && input) {
    fillPlatformPhrase(
      label,
      field.platform,
      on ? input.dataset.channelLabel || "" : input.dataset.videoLabel || "",
    );
  }
}

function fillLedgerAddressFields(settings) {
  const saved = settings || {};
  LEDGER_ADDRESS_FIELDS.forEach((field) => {
    const input = $(field.inputId);
    const follow = $(field.followId);
    const on = Boolean(saved[field.followKey]);
    if (follow) follow.checked = on;
    if (input) input.value = on ? (saved[field.channelKey] || "") : (saved[field.watchKey] || "");
    ledgerAddressMode(field);
  });
  const kick = $("ledger-kick");
  if (kick) kick.value = saved.kick_watch || "";
}

function resetLedgerAddressFields() {
  LEDGER_ADDRESS_FIELDS.forEach((field) => {
    const input = $(field.inputId);
    const follow = $(field.followId);
    if (input) input.value = "";
    if (follow) follow.checked = false;
    ledgerAddressMode(field);
  });
  const kick = $("ledger-kick");
  if (kick) kick.value = "";
}

function presetPlatformIds(preset) {
  const ids = [];
  if (preset.youtube_channel) ids.push("youtube");
  if (preset.rumble_channel) ids.push("rumble");
  if (preset.kick_watch) ids.push("kick");
  return ids;
}

function renderPresetList(presets) {
  const list = $("preset-list");
  const empty = $("preset-empty");
  if (!list) return;
  list.replaceChildren();
  const items = presets || [];
  if (empty) empty.hidden = items.length > 0;
  items.forEach((preset) => {
    const row = el("li");
    const meta = el("div", "preset-meta");
    meta.appendChild(el("p", "preset-name", preset.name));
    const channels = el("p", "preset-channels");
    const ids = presetPlatformIds(preset);
    if (!ids.length) channels.textContent = preset.tiers_enabled ? "No channels · goals" : "No channels";
    else {
      ids.forEach((id, index) => {
        if (index) channels.appendChild(document.createTextNode(", "));
        channels.appendChild(platformLabel(id));
      });
      if (preset.tiers_enabled) channels.appendChild(document.createTextNode(" · goals"));
    }
    const script = (state.blueScripts || []).find((item) => item.id === preset.script_id);
    if (script && script.name) channels.appendChild(document.createTextNode(" · " + script.name));
    meta.appendChild(channels);
    const edit = el("button", null, "Edit");
    edit.type = "button";
    edit.setAttribute("aria-label", "Edit " + preset.name);
    edit.addEventListener("click", () => editPreset(preset));
    const remove = el("button", null, "Remove");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove " + preset.name);
    remove.addEventListener("click", () => deletePreset(preset));
    const actions = el("div", "preset-actions");
    actions.append(edit, remove);
    row.appendChild(meta);
    row.appendChild(actions);
    list.appendChild(row);
  });
}

function fillPresetScriptSelect(scripts) {
  const select = $("preset-script");
  if (!select) return;
  const current = select.value;
  select.replaceChildren();
  const none = el("option", null, "None");
  none.value = "";
  select.appendChild(none);
  (scripts || []).forEach((script) => {
    const option = el("option", null, script.name);
    option.value = script.id;
    select.appendChild(option);
  });
  const kept = [...select.options].some((option) => option.value === current);
  select.value = kept ? current : "";
}

function renderBlueList(scripts) {
  const list = $("blue-list");
  const empty = $("blue-empty");
  if (!list) return;
  list.replaceChildren();
  const items = scripts || [];
  if (empty) empty.hidden = items.length > 0;
  items.forEach((script) => {
    const row = el("li");
    const meta = el("div", "preset-meta");
    meta.appendChild(el("p", "preset-name", script.name));
    const edit = el("button", null, "Edit");
    edit.type = "button";
    edit.setAttribute("aria-label", "Edit " + script.name);
    edit.addEventListener("click", () => editBlue(script));
    const remove = el("button", null, "Remove");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove " + script.name);
    remove.addEventListener("click", () => deleteBlue(script));
    const actions = el("div", "preset-actions");
    actions.append(edit, remove);
    row.appendChild(meta);
    row.appendChild(actions);
    list.appendChild(row);
  });
}

function showBlueProblems(problems) {
  const node = $("blue-problems");
  if (!node) return;
  const items = problems || [];
  if (!items.length) {
    node.hidden = true;
    node.textContent = "";
    return;
  }
  node.hidden = false;
  node.textContent = items.map((item) => "Line " + item.line + ": " + item.message).join(" ");
}

function refreshBlueScripts() {
  return api("/api/blue").then((payload) => {
    state.blueScripts = (payload && payload.scripts) || [];
    renderBlueList(state.blueScripts);
    fillPresetScriptSelect(state.blueScripts);
    renderPresetList(state.presets);
    return state.blueScripts;
  });
}

function saveBlue() {
  const form = $("blue-form");
  const saving = form ? form.querySelector("button.primary") : null;
  if (saving) saving.disabled = true;
  const name = ($("blue-name") && $("blue-name").value) || "";
  const source = ($("blue-source") && $("blue-source").value) || "";
  api("/api/blue", {
    method: "POST",
    body: JSON.stringify({ action: "save", name, source }),
  })
    .then((payload) => {
      state.blueScripts = (payload && payload.scripts) || [];
      renderBlueList(state.blueScripts);
      fillPresetScriptSelect(state.blueScripts);
      if (form) form.reset();
      refreshBlueEditor(false);
      showBlueProblems(payload && payload.problems);
      toast("Saved BLUE script " + ((payload.script && payload.script.name) || name) + ".");
    })
    .catch((error) => toast(error.message, true))
    .finally(() => {
      if (saving) saving.disabled = false;
    });
}

function editBlue(script) {
  showBlueProblems([]);
  const name = $("blue-name");
  const source = $("blue-source");
  if (name) name.value = script.name || "";
  if (source) source.value = script.source || "";
  if (source) source.scrollTop = 0;
  refreshBlueEditor(true);
  if (name) name.focus();
}

function deleteBlue(script) {
  const sure = window.confirm(
    "Remove " + script.name + "? Presets and ledgers that use it stop running it.",
  );
  if (!sure) return;
  api("/api/blue", {
    method: "POST",
    body: JSON.stringify({ action: "delete", id: script.id }),
  })
    .then((payload) => {
      state.blueScripts = (payload && payload.scripts) || [];
      renderBlueList(state.blueScripts);
      fillPresetScriptSelect(state.blueScripts);
      return refreshPresets();
    })
    .then(() => toast("Removed BLUE script " + script.name + "."))
    .catch((error) => toast(error.message, true));
}

function readBlueFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result || "")));
    reader.addEventListener("error", () => reject(new Error("That file could not be read.")));
    reader.readAsText(file);
  });
}

function importBlueFile(file) {
  if (!file) return;
  readBlueFile(file)
    .then((text) => {
      if (text.length > 16000) {
        toast("That BLUE script is too long.", true);
        return;
      }
      const source = $("blue-source");
      if (source) source.value = text;
      showBlueProblems([]);
      refreshBlueEditor(true);
    })
    .catch((error) => toast(error.message, true));
}

function exportBlue() {
  const source = ($("blue-source") && $("blue-source").value) || "";
  const raw = (($("blue-name") && $("blue-name").value) || "script").trim() || "script";
  const safe = raw.replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "") || "script";
  const blob = new Blob([source], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = safe + ".blue";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// BLUE editor: a highlighted <pre> under a transparent textarea. The textarea
// is still the editor, so typing, IME, undo, import, and export are unchanged.
const blueEditor = {
  grammar: window.BlueHighlight ? window.BlueHighlight.compile(null) : null,
  problems: new Map(),
  timer: 0,
  asked: 0,
  lines: 0,
};

function loadBlueGrammar() {
  if (!window.BlueHighlight || blueEditor.loaded) return;
  blueEditor.loaded = true;
  api("/api/blue/grammar")
    .then((grammar) => {
      blueEditor.grammar = window.BlueHighlight.compile(grammar);
      paintBlue();
    })
    .catch(() => {
      blueEditor.loaded = false;
    });
}

function paintBlue() {
  const source = $("blue-source");
  const pre = $("blue-highlight");
  const gutter = $("blue-gutter-lines");
  if (!source || !pre || !gutter || !window.BlueHighlight) return;
  const count = window.BlueHighlight.render(document, pre, source.value, blueEditor.grammar, blueEditor.problems);
  const rows = [];
  for (let number = 1; number <= count; number += 1) {
    const row = el("div", "blue-gutter-line", String(number));
    const problem = blueEditor.problems.get(number);
    if (problem) {
      row.classList.add("bad");
      row.title = "Line " + number + ": " + problem;
    }
    rows.push(row);
  }
  gutter.replaceChildren(...rows);
  syncBlueScroll();
}

function syncBlueScroll() {
  const source = $("blue-source");
  const pre = $("blue-highlight");
  const gutter = $("blue-gutter-lines");
  if (!source || !pre || !gutter) return;
  pre.style.transform = "translate(" + -source.scrollLeft + "px, " + -source.scrollTop + "px)";
  gutter.style.transform = "translateY(" + -source.scrollTop + "px)";
}

function setBlueProblems(problems) {
  blueEditor.problems = new Map();
  (problems || []).forEach((item) => {
    const before = blueEditor.problems.get(item.line);
    blueEditor.problems.set(item.line, before ? before + " " + item.message : item.message);
  });
}

function inspectBlueSoon() {
  window.clearTimeout(blueEditor.timer);
  blueEditor.timer = window.setTimeout(inspectBlue, 300);
}

function inspectBlue() {
  const source = ($("blue-source") && $("blue-source").value) || "";
  const asked = ++blueEditor.asked;
  if (!source.trim()) {
    setBlueProblems([]);
    showBlueProblems([]);
    paintBlue();
    return;
  }
  api("/api/blue/inspect", { method: "POST", body: JSON.stringify({ source }) })
    .then((payload) => {
      if (asked !== blueEditor.asked) return;
      const problems = (payload && payload.problems) || [];
      setBlueProblems(problems);
      showBlueProblems(problems);
      paintBlue();
    })
    .catch(() => {});
}

// After the textarea's text was set from code: import, edit, or save.
function refreshBlueEditor(check) {
  window.clearTimeout(blueEditor.timer);
  blueEditor.asked += 1;
  setBlueProblems([]);
  paintBlue();
  if (check) inspectBlueSoon();
}

const BLUE_TEST_KINDS = { youtube: "super_chat", rumble: "rant", kick: "kicks", other: "donation" };

function fillBlueTestForm() {
  const ledger = $("blue-test-ledger");
  if (ledger) {
    const streams = (state.stream && state.stream.streams) || { active_id: "", tabs: [] };
    const tabs = streams.tabs || [];
    const keep = ledger.value;
    ledger.replaceChildren(...tabs.map((tab) => {
      const option = el("option", "", tab.title || "Stream");
      option.value = tab.id;
      return option;
    }));
    const wanted = tabs.some((tab) => tab.id === keep) && ledger.dataset.picked === "1" ? keep : streams.active_id;
    if (wanted) ledger.value = wanted;
  }
  const kind = $("blue-test-kind");
  if (kind && !kind.options.length) {
    const kinds = (state.meta && state.meta.kinds) || [];
    kind.replaceChildren(...kinds.map((item) => {
      const option = el("option", "", item.label);
      option.value = item.id;
      return option;
    }));
    syncBlueTestKind();
  }
  syncBlueTestEvent();
}

function syncBlueTestKind() {
  const platform = $("blue-test-platform");
  const kind = $("blue-test-kind");
  const currency = $("blue-test-currency");
  if (!platform || !kind) return;
  const wanted = BLUE_TEST_KINDS[platform.value] || "donation";
  if ([...kind.options].some((option) => option.value === wanted)) kind.value = wanted;
  if (currency && (currency.value === "" || currency.value === "USD" || currency.value === "KICKS")) {
    currency.value = platform.value === "kick" ? "KICKS" : "USD";
  }
}

function syncBlueTestEvent() {
  const event = ($("blue-test-event") && $("blue-test-event").value) || "donation";
  document.querySelectorAll("#blue-test-form [data-blue-for]").forEach((label) => {
    label.hidden = !label.dataset.blueFor.split(" ").includes(event);
  });
  const tier = $("blue-test-tier-label");
  if (tier) tier.textContent = event === "goal" ? "Tier reached" : "TIER";
}

function detectBlueTestEvent() {
  const line = ($("blue-test-line") && $("blue-test-line").value) || "";
  const found = /^\s*on\s+(donation|timer|goal)\b/i.exec(line);
  const select = $("blue-test-event");
  if (found && select && select.value !== found[1].toLowerCase()) {
    select.value = found[1].toLowerCase();
    syncBlueTestEvent();
  }
}

function runBlueTest(whole) {
  const value = (id) => ($(id) && $(id).value) || "";
  const source = whole ? value("blue-source") : value("blue-test-line");
  if (!source.trim()) {
    toast(whole ? "The script is empty." : "Type a BLUE line to test.", true);
    return;
  }
  const event = value("blue-test-event");
  const body = {
    source,
    event,
    stream_id: value("blue-test-ledger"),
    minutes: value("blue-test-minutes"),
    time: value("blue-test-time"),
    total: value("blue-test-total"),
    tier: value("blue-test-tier"),
  };
  if (event !== "timer") {
    Object.assign(body, {
      amount: value("blue-test-amount"),
      currency: value("blue-test-currency"),
      donor: value("blue-test-donor"),
      platform: value("blue-test-platform"),
      kind: value("blue-test-kind"),
    });
  }
  api("/api/blue/test", { method: "POST", body: JSON.stringify(body) })
    .then((result) => renderBlueTest(result))
    .catch((error) => toast(error.message, true));
}

function renderBlueTest(result) {
  const box = $("blue-test-result");
  if (!box || !result) return;
  const parts = [el("p", "blue-dry-run", "Dry run - nothing is sent.")];
  const ledger = result.ledger || {};
  const facts = [
    "Ledger " + (ledger.title || "Stream"),
    "TOTAL " + (ledger.total || "unknown") + (ledger.total_from === "typed" ? " (typed)" : ""),
    "TIER " + ledger.tier + (ledger.tier_from === "typed" ? " (typed)" : ""),
    "TIME " + (result.time || ""),
  ];
  if (result.minutes != null) facts.push("MINUTES " + result.minutes);
  facts.push(ledger.script ? "runs " + ledger.script : "no script on this ledger");
  parts.push(el("p", "hint", facts.join(" · ")));
  (result.problems || []).forEach((item) => {
    parts.push(el("p", "blue-test-bad", "Line " + item.line + " is skipped: " + item.message));
  });
  const lines = result.lines || [];
  if (!lines.length && !(result.problems || []).length) parts.push(el("p", "hint", "There is no line to run."));
  lines.forEach((line) => parts.push(renderBlueTestLine(line, lines.length > 1)));
  (result.notes || []).forEach((note) => parts.push(el("p", "hint", note)));
  box.replaceChildren(...parts);
  box.hidden = false;
}

function renderBlueTestLine(line, numbered) {
  const card = el("div", "blue-test-line");
  const where = numbered ? "Line " + line.line + ": " : "";
  if (!line.applies) {
    card.appendChild(el("p", "hint", where + "ON " + line.event.toUpperCase() + " does not run for this event."));
    return card;
  }
  const head = el("p", line.matched ? "blue-test-good" : "blue-test-bad",
    where + (line.matched ? "Matched." : "Did not match, so nothing would happen."));
  card.appendChild(head);
  if (line.timer === "clock") card.appendChild(el("p", "hint", "Uses TIME, so it runs once for each new clock minute."));
  if (line.timer === "elapsed") card.appendChild(el("p", "hint", "Runs once for each minute since connect."));
  const groups = line.groups || [];
  if (!groups.length) card.appendChild(el("p", "hint", "No IF, so it always matches."));
  groups.forEach((group, index) => {
    if (index > 0) card.appendChild(el("p", "blue-test-or", "OR"));
    const list = el("ul", "blue-test-checks");
    group.forEach((item) => {
      const row = el("li", item.result ? "blue-test-good" : "blue-test-bad");
      row.appendChild(el("span", "blue-test-mark", item.result ? "yes" : "no"));
      row.appendChild(el("code", "", item.text));
      row.appendChild(document.createTextNode(" "));
      row.appendChild(el("span", "hint", "saw " + item.saw));
      list.appendChild(row);
    });
    card.appendChild(list);
  });
  (line.actions || []).forEach((action) => {
    const row = el("div", "blue-test-action " + (action.would === "skip" ? "skip" : "go"));
    row.appendChild(el("p", "", action.would === "skip" ? "Would skip: " + action.why : action.why));
    if (action.final) row.appendChild(el("pre", "blue-test-text", action.final));
    (action.checks || []).forEach((check) => {
      if (check.message === action.why) return;
      row.appendChild(el("p", check.ok ? "hint" : "blue-test-bad", check.name + ": " + check.message));
    });
    card.appendChild(row);
  });
  return card;
}

function setupBlueEditor() {
  const source = $("blue-source");
  if (source) {
    source.addEventListener("input", () => {
      paintBlue();
      inspectBlueSoon();
    });
    source.addEventListener("scroll", syncBlueScroll);
    paintBlue();
  }
  const form = $("blue-test-form");
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      runBlueTest(false);
    });
  }
  const whole = $("blue-test-use-script");
  if (whole) whole.addEventListener("click", () => runBlueTest(true));
  const line = $("blue-test-line");
  if (line) line.addEventListener("input", detectBlueTestEvent);
  const event = $("blue-test-event");
  if (event) event.addEventListener("change", syncBlueTestEvent);
  const platform = $("blue-test-platform");
  if (platform) platform.addEventListener("change", syncBlueTestKind);
  const ledger = $("blue-test-ledger");
  if (ledger) ledger.addEventListener("change", () => { ledger.dataset.picked = "1"; });
}

function refreshPresets() {
  return api("/api/presets").then((payload) => {
    state.presets = (payload && payload.presets) || [];
    renderPresetList(state.presets);
    return state.presets;
  });
}

function presetFormBody() {
  return {
    action: "save",
    name: $("preset-name").value,
    youtube_channel: $("preset-youtube").value.trim(),
    rumble_channel: $("preset-rumble").value.trim(),
    kick_watch: $("preset-kick").value.trim(),
    tiers_enabled: $("tiers-enabled").checked,
    goal_tiers: collectTiers(),
    script_id: ($("preset-script") && $("preset-script").value) || "",
  };
}

let presetBusy = false;

function openLedgerId() {
  const streams = state.stream && state.stream.streams;
  return (streams && streams.active_id) || "";
}

function openLedgerTitle() {
  return (state.stream && state.stream.title) || "Stream";
}

function syncPresetApply() {
  const apply = $("preset-apply");
  if (!apply) return;
  const open = Boolean(openLedgerId());
  apply.disabled = presetBusy || !open;
  apply.title = open ? "Save this preset, then write it onto " + openLedgerTitle() : "No ledger is open.";
}

function setPresetBusy(busy) {
  presetBusy = busy;
  const form = $("preset-form");
  const saving = form ? form.querySelector("button.primary") : null;
  if (saving) saving.disabled = busy;
  syncPresetApply();
}

function showPresetError(err, fallback) {
  const error = $("preset-error");
  if (!error) {
    toast((err && err.message) || fallback, true);
    return;
  }
  error.hidden = false;
  error.textContent = (err && err.message) || fallback;
}

function clearPresetError() {
  const error = $("preset-error");
  if (error) {
    error.hidden = true;
    error.textContent = "";
  }
}

function resetPresetForm() {
  const form = $("preset-form");
  if (form) form.reset();
  renderTierFields([]);
  fillPresetScriptSelect(state.blueScripts);
}

function savePreset() {
  if (presetBusy) return;
  setPresetBusy(true);
  clearPresetError();
  const body = presetFormBody();
  api("/api/presets", { method: "POST", body: JSON.stringify(body) })
    .then((payload) => {
      state.presets = (payload && payload.presets) || [];
      renderPresetList(state.presets);
      resetPresetForm();
      toast("Saved streamer preset " + ((payload.preset && payload.preset.name) || body.name) + ".");
    })
    .catch((err) => showPresetError(err, "Could not save that preset."))
    .finally(() => setPresetBusy(false));
}

// Apply saves the form first, so the ledger gets what is on screen and the
// preset keeps the same channels, goals, and script. Plain Save never applies.
function applyPresetToOpenLedger() {
  if (presetBusy) return;
  if (!openLedgerId()) {
    toast("No ledger is open.", true);
    return;
  }
  const body = presetFormBody();
  const label = String(body.name || "").trim() || "this preset";
  const ledger = openLedgerTitle();
  const sure = window.confirm(
    "Save " + label + " and apply it to " + ledger + "? That ledger's channels, goals, and BLUE script are replaced. Its donations stay.",
  );
  if (!sure) return;
  setPresetBusy(true);
  clearPresetError();
  let savedName = label;
  api("/api/presets", { method: "POST", body: JSON.stringify(body) })
    .then((payload) => {
      state.presets = (payload && payload.presets) || [];
      renderPresetList(state.presets);
      const preset = payload && payload.preset;
      if (!preset || !preset.id) throw new Error("Could not save that preset.");
      savedName = preset.name || label;
      return api("/api/presets", {
        method: "POST",
        body: JSON.stringify({ action: "apply", id: preset.id }),
      }).catch((err) => {
        throw new Error("Saved streamer preset " + savedName + ". " + ((err && err.message) || "It was not applied to the open ledger."));
      });
    })
    .then((payload) => {
      state.presets = (payload && payload.presets) || state.presets;
      renderPresetList(state.presets);
      if (payload && payload.settings) applySettingsToForm(payload.settings);
      resetPresetForm();
      toast("Saved streamer preset " + savedName + " and applied it to " + ledger + ".");
      return refresh().catch(() => {});
    })
    .catch((err) => showPresetError(err, "Could not apply that preset."))
    .finally(() => setPresetBusy(false));
}

function editPreset(preset) {
  const error = $("preset-error");
  if (error) {
    error.hidden = true;
    error.textContent = "";
  }
  $("preset-name").value = preset.name || "";
  $("preset-youtube").value = preset.youtube_channel || "";
  $("preset-rumble").value = preset.rumble_channel || "";
  $("preset-kick").value = preset.kick_watch || "";
  const script = $("preset-script");
  if (script) script.value = preset.script_id || "";
  if ($("tiers-enabled")) $("tiers-enabled").checked = Boolean(preset.tiers_enabled);
  renderTierFields(preset.goal_tiers);
  const name = $("preset-name");
  if (name) name.focus();
}

function deletePreset(preset) {
  const sure = window.confirm(
    "Remove " + preset.name + "? Ledgers you already opened keep their addresses and goals.",
  );
  if (!sure) return;
  api("/api/presets", {
    method: "POST",
    body: JSON.stringify({ action: "delete", id: preset.id }),
  })
    .then((payload) => {
      state.presets = (payload && payload.presets) || [];
      renderPresetList(state.presets);
      toast("Removed streamer preset " + preset.name + ".");
    })
    .catch((err) => toast(err.message, true));
}

function fillLedgerPresetSelect(presets) {
  const select = $("ledger-preset");
  if (!select) return;
  select.replaceChildren();
  const none = el("option", null, "None");
  none.value = "";
  select.appendChild(none);
  (presets || []).forEach((preset) => {
    const option = el("option", null, preset.name);
    option.value = preset.id;
    select.appendChild(option);
  });
  select.value = "";
}

function applyLedgerPreset(preset) {
  LEDGER_ADDRESS_FIELDS.forEach((field) => {
    const follow = $(field.followId);
    const input = $(field.inputId);
    const text = String((preset && preset[field.channelKey]) || "").trim();
    if (follow) follow.checked = Boolean(text);
    if (input) input.value = text;
    ledgerAddressMode(field);
  });
  const kick = $("ledger-kick");
  if (kick) kick.value = String((preset && preset.kick_watch) || "").trim();
}

function ledgerAddressPayload() {
  const body = {
    kick_watch: ($("ledger-kick") && $("ledger-kick").value.trim()) || "",
  };
  LEDGER_ADDRESS_FIELDS.forEach((field) => {
    const input = $(field.inputId);
    const follow = $(field.followId);
    const text = input ? input.value.trim() : "";
    if (follow && follow.checked) {
      body[field.channelKey] = text;
      body[field.followKey] = true;
    } else {
      body[field.watchKey] = text;
      body[field.followKey] = false;
    }
  });
  const select = $("ledger-preset");
  const presetId = select ? select.value : "";
  const preset = presetId
    ? (state.presets || []).find((item) => item.id === presetId)
    : null;
  if (preset) {
    body.tiers_enabled = Boolean(preset.tiers_enabled);
    body.goal_tiers = Array.isArray(preset.goal_tiers) ? preset.goal_tiers : [];
    body.blue_script_id = preset.script_id || "";
  }
  return body;
}

const LEDGER_URLS_FRESH = "This ledger starts with a blank name and no donations. Each ledger keeps its own YouTube, Rumble, and Kick address. API keys stay shared. For YouTube or Rumble, turn on Follow the live stream and that field asks for a channel URL instead of a video. Kick is already a channel. Leave any of these blank and fill them in later.";
const LEDGER_URLS_AGAIN = "Each ledger keeps its own YouTube, Rumble, and Kick address. API keys stay shared. For YouTube or Rumble, turn on Follow the live stream and that field asks for a channel URL instead of a video. Kick is already a channel. Leave any of these blank.";

function promptLedgerUrls(options) {
  const fresh = !options || options.fresh !== false;
  const dialog = $("ledger-urls");
  const form = $("ledger-urls-form");
  const skip = $("ledger-urls-skip");
  if (!dialog || !form || typeof dialog.showModal !== "function") {
    toast("New ledger started empty. Add its platform addresses from YouTube, Rumble, or Kick.");
    return Promise.resolve();
  }
  if (dialog.open) return Promise.resolve();
  const intro = $("ledger-urls-intro");
  if (intro) intro.textContent = fresh ? LEDGER_URLS_FRESH : LEDGER_URLS_AGAIN;
  resetLedgerAddressFields();
  const error = $("ledger-urls-error");
  error.hidden = true;
  error.textContent = "";
  const saved = fresh
    ? Promise.resolve(null)
    : api("/api/settings").then((settings) => {
      applySettingsToForm(settings);
      return settings;
    });
  return saved
    .then(() => refreshPresets())
    .catch(() => {
      state.presets = [];
      return [];
    })
    .then((presets) => {
      fillLedgerPresetSelect(presets);
      if (!fresh) fillLedgerAddressFields(state.settings || {});
      return openLedgerUrlDialog(dialog, form, skip, error);
    });
}

function openLedgerUrlDialog(dialog, form, skip, error) {
  return new Promise((resolve) => {
    let settled = false;
    const onToggle = (event) => {
      const field = LEDGER_ADDRESS_FIELDS.find((item) => item.followId === event.target.id);
      if (field) ledgerAddressMode(field);
    };
    const onPreset = () => {
      const select = $("ledger-preset");
      const id = select ? select.value : "";
      if (!id) {
        resetLedgerAddressFields();
        return;
      }
      const preset = (state.presets || []).find((item) => item.id === id);
      if (preset) applyLedgerPreset(preset);
    };
    const finish = () => {
      if (settled) return;
      settled = true;
      form.removeEventListener("submit", onSubmit);
      dialog.removeEventListener("close", onClose);
      if (skip) skip.removeEventListener("click", onSkip);
      const select = $("ledger-preset");
      if (select) select.removeEventListener("change", onPreset);
      LEDGER_ADDRESS_FIELDS.forEach((field) => {
        const follow = $(field.followId);
        if (follow) follow.removeEventListener("change", onToggle);
      });
      resolve();
    };
    const onClose = () => finish();
    const onSkip = () => {
      if (!settled) dialog.close();
    };
    const onSubmit = (event) => {
      event.preventDefault();
      if (settled) return;
      const body = ledgerAddressPayload();
      const saving = form.querySelector("button.primary");
      if (saving) saving.disabled = true;
      api("/api/settings", { method: "PUT", body: JSON.stringify(body) })
        .then((settings) => {
          applySettingsToForm(settings);
          if (!settled) dialog.close();
        })
        .catch((err) => {
          error.hidden = false;
          error.textContent = err.message || "Could not save those addresses.";
        })
        .finally(() => {
          if (saving) saving.disabled = false;
        });
    };
    form.addEventListener("submit", onSubmit);
    dialog.addEventListener("close", onClose);
    if (skip) skip.addEventListener("click", onSkip);
    LEDGER_ADDRESS_FIELDS.forEach((field) => {
      const follow = $(field.followId);
      if (follow) follow.addEventListener("change", onToggle);
    });
    const select = $("ledger-preset");
    if (select) select.addEventListener("change", onPreset);
    dialog.showModal();
    if (select) select.focus();
    else {
      const first = $("ledger-youtube");
      if (first) first.focus();
    }
  });
}

function archiveOpenLedger() {
  const tabs = state.stream && state.stream.streams && state.stream.streams.tabs;
  const last = !tabs || tabs.length < 2;
  const name = (state.stream && state.stream.title) || "Stream";
  const sure = window.confirm(last
    ? "Save " + name + " to the archive and close it? A new blank ledger opens."
    : "Save " + name + " to the archive and close it? Its donations leave the open ledgers. The other ledgers stay.");
  if (!sure) return Promise.resolve();
  return enqueueStream(async () => {
    await commitStreamTitle();
    const result = await api("/api/export/archive", { method: "POST", body: "{}" });
    await reloadOpenStream();
    const count = Number(result.count) || 0;
    const noun = count === 1 ? "donation" : "donations";
    toast("Saved " + count + " " + noun + " to the archive and closed that ledger.");
  });
}

function closeStream(id, title) {
  const tabs = state.stream && state.stream.streams && state.stream.streams.tabs;
  const last = !tabs || tabs.length < 2;
  const name = title || "Stream";
  const tab = (tabs || []).find((item) => item.id === id);
  const entries = tab ? Number(tab.entries) : 0;
  if (entries > 0 && window.confirm(
    "Archive " + name + " before closing it? Its donations are saved, then this ledger closes."
  )) {
    return enqueueStream(async () => {
      await commitStreamTitle();
      const result = await api("/api/export/archive", {
        method: "POST",
        body: JSON.stringify({ id }),
      });
      await reloadOpenStream();
      const count = Number(result.count) || 0;
      const noun = count === 1 ? "donation" : "donations";
      toast("Saved " + count + " " + noun + " to the archive and closed that ledger.");
    });
  }
  const sure = window.confirm(last
    ? "Close " + name + "? This ledger's donations are removed. A new blank ledger opens."
    : "Close " + name + "? This ledger's donations are removed. The other ledgers stay.");
  if (!sure) return Promise.resolve();
  return enqueueStream(async () => {
    await commitStreamTitle();
    await api("/api/streams", { method: "POST", body: JSON.stringify({ action: "close", id }) });
    await reloadOpenStream();
    toast("Closed that ledger. Its donations were removed.");
  });
}

function renderStream(report) {
  state.stream = report;
  renderStreamTabs(report);
  syncPresetApply();
  const title = $("stream-title");
  const naming = $("ledger-name");
  if (
    title
    && !(naming && naming.open)
    && document.activeElement !== title
    && title.value !== (report.title || "")
  ) {
    title.value = report.title || "";
  }
  const countLabel = report.count === 1 ? "entry" : "entries";
  const line = streamChatLine(Number(report.total_usd || 0), report);
  $("stream-summary").textContent = `${report.count} ${countLabel}. ${line}`;
  $("stream-breakdown").textContent = (report.by_type || [])
    .map((item) => `${item.count} ${item.type} · ${formatStreamUsd(item.usd)}`)
    .join("  ·  ");
  $("stream-last").textContent = report.last ? `Last: ${report.last}` : "Last: none";
  $("stream-scope").textContent = "Counting every donation on this ledger. Other ledgers are separate.";
  $("top-donors").hidden = !report.show_top_donors;
  const statusDonors = $("status-donors");
  const statusDonorLine = $("status-donor-line");
  if (statusDonors) statusDonors.hidden = !report.show_top_donors;
  if (statusDonorLine) {
    statusDonorLine.textContent = report.donor_bar || "";
    statusDonorLine.title = statusDonorLine.textContent;
  }
  const board = report.donors || { total: 0, rows: [] };
  const lead = (board.rows || [])[0];
  if (!board.total) $("donor-summary").textContent = "No gifts yet";
  else if (lead) {
    const more = board.total > board.rows.length ? ` · top ${board.rows.length} of ${board.total}` : "";
    $("donor-summary").textContent = `${donorName(lead.donor)} leads at ${formatStreamUsd(lead.usd)}${more}`;
  } else $("donor-summary").textContent = `${board.total} donors`;
  $("donor-bar").value = report.donor_bar || "";
  const list = $("donor-list");
  list.replaceChildren();
  (board.rows || []).forEach((donor, index) => {
    const name = el("div", "donor-name");
    const gifts = donor.count === 1 ? "gift" : "gifts";
    const meta = el("p", "donor-meta");
    meta.appendChild(document.createTextNode(`${donor.count} ${gifts}`));
    (donor.platforms || []).forEach((platform) => {
      meta.appendChild(document.createTextNode(" · "));
      meta.appendChild(platformLabel(platform, platform));
    });
    name.append(el("p", null, donorName(donor.donor)), meta);
    const item = document.createElement("li");
    item.append(el("span", "donor-rank", String(index + 1)), name, el("span", "donor-usd", formatStreamUsd(donor.usd)));
    list.appendChild(item);
  });
  const thanks = report.thanks || [];
  $("tier-thanks").hidden = thanks.length === 0;
  const thanksList = $("thanks-list");
  thanksList.replaceChildren();
  thanks.forEach((item) => {
    const row = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "icon-button";
    button.setAttribute("aria-label", "Copy thank you");
    button.title = "Copy thank you";
    button.appendChild(icon("copy"));
    button.addEventListener("click", () => copyText(item.copy || item.line, "Thank you copied."));
    const send = document.createElement("button");
    send.type = "button";
    send.className = "icon-button";
    send.setAttribute("aria-label", "Send thank you");
    send.title = "Send thank you";
    send.appendChild(icon("send"));
    send.addEventListener("click", () => sendChatText(item.copy || item.line));
    row.append(el("p", null, item.line), button, send);
    thanksList.appendChild(row);
  });
  updateStreamPreview();
  renderUtility(report);
  const activeLedger = report.streams && report.streams.active_id;
  if (utilityOn() && activeLedger && activeLedger !== utilityLedgerId) {
    utilityLedgerId = activeLedger;
    syncUtilityChats();
  }
}

function syncManualUsd() {
  const currency = ($("manual-currency").value || DEFAULT_CURRENCY).trim().toUpperCase();
  if (!currency || currency === "USD") {
    const native = $("manual-native").value.trim();
    if (native) $("manual-amount").value = native;
  }
  updateStreamPreview();
}

function updateStreamPreview() {
  const report = state.stream;
  if (!report || !$("new-total")) return;
  const typed = parseAmount($("manual-amount") && $("manual-amount").value);
  const base = editingId == null ? 0 : (editingUsd || 0);
  const extra = round2((typed || 0) - base);
  const preview = round2(Number(report.total_usd || 0) + extra);
  $("new-total").value = preview.toFixed(2);
  $("chat-line").value = streamChatLine(preview, report);
  const statusTotal = $("status-total-line");
  if (statusTotal) {
    statusTotal.textContent = $("chat-line").value;
    statusTotal.title = statusTotal.textContent;
  }
  const utilityTotal = $("utility-total");
  if (utilityTotal) {
    utilityTotal.textContent = $("chat-line").value;
    utilityTotal.title = utilityTotal.textContent;
  }
}

function tierField(label, className, value, type, placeholder) {
  const wrap = el("label", null, label);
  const input = document.createElement("input");
  input.className = className;
  input.type = type;
  input.value = value;
  if (placeholder) input.placeholder = placeholder;
  if (type === "number") {
    input.min = "0";
    input.step = "0.01";
    input.inputMode = "decimal";
  }
  wrap.appendChild(input);
  return wrap;
}

function renderTierFields(tiers) {
  const host = $("goal-tiers");
  if (!host) return;
  const source = Array.from({ length: 5 }, (_, index) => (tiers && tiers[index]) || {
    enabled: false,
    amount: null,
    name: "",
    bonus: "",
  });
  host.replaceChildren();
  source.forEach((tier, index) => {
    const row = el("div", "tier-grid");
    const toggle = el("label", "check");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "tier-on";
    box.checked = Boolean(tier.enabled);
    toggle.append(box, document.createTextNode(` Tier ${index + 1}`));
    row.append(
      toggle,
      tierField("Amount USD", "tier-amount", tier.amount == null ? "" : String(tier.amount), "number", "25.00"),
      tierField("Name", "tier-name", tier.name || "", "text", TIER_PLACEHOLDERS[index] || "Name"),
      tierField("Bonus", "tier-bonus", tier.bonus || "", "text", "optional"),
    );
    host.appendChild(row);
  });
}

function collectTiers() {
  return [...document.querySelectorAll("#goal-tiers .tier-grid")].map((row) => {
    const amount = row.querySelector(".tier-amount").value.trim();
    const number = amount === "" ? null : Number(amount);
    return {
      enabled: row.querySelector(".tier-on").checked,
      amount: number != null && Number.isFinite(number) ? number : null,
      name: row.querySelector(".tier-name").value,
      bonus: row.querySelector(".tier-bonus").value,
    };
  });
}

const WATCH_PLATFORMS = ["youtube", "rumble", "kick"];
const WATCH_OPEN_KEY = "stream-donations-watch-open";
const WATCH_PLATFORM_KEY = "stream-donations-watch-platform";
const WATCH_TIMES_KEY = "stream-donations-watch-times";
const WATCH_MUTED_KEY = "stream-donations-watch-muted";
const UTILITY_KEY = "stream-donations-utility";
const UTILITY_CHAT_KEY = "stream-donations-utility-chats";
const UTILITY_CHAT_ZOOM_MIN = 0.8;
const UTILITY_CHAT_ZOOM_MAX = 1.6;
const UTILITY_CHAT_ZOOM_STEP = 0.1;
const WATCH_RESERVED = ["api", "settings", "dashboard", "browse", "categories", "following"];

const WATCH_CHAT_ICONS = PLATFORM_ICONS;
const WATCH_CHAT_LABELS = PLATFORM_NAMES;

let watchBound = false;
let watchSignature = "";
let watchSerial = 0;
let watchBareUrl = "";
let watchFrameSrc = "";
let watchPlaying = false;
let watchMuted = false;
let watchChatTimer = 0;
let watchChatCursor = 0;
let watchChatGeneration = -1;
let watchChatLoading = false;
let utilityLedgerId = "";
const utilityChatUrls = { youtube: "", kick: "", rumble: "" };
let utilityChatSerial = 0;
let utilityChatCursor = 0;
let utilityChatGeneration = -1;
let utilityChatWatchTimer = 0;
let utilityChatWatchLoading = false;
let utilityLedgerTail = "";
const utilityChatPeek = { youtube: false, kick: false, rumble: false };

function watchAddressSignature(settings) {
  const source = settings || {};
  return [
    source.youtube_watch || "",
    source.youtube_channel || "",
    source.youtube_follow_live ? "1" : "0",
    source.rumble_watch || "",
    source.rumble_channel || "",
    source.rumble_follow_live ? "1" : "0",
    source.kick_watch || "",
    source.kick_follow_live ? "1" : "0",
  ].join("\n");
}

function selectedWatchPlatform() {
  const select = $("watch-platforms");
  const platform = select && select.value;
  return WATCH_PLATFORMS.includes(platform) ? platform : "";
}

function markSourceSelect(select, mark, chosen) {
  if (select) {
    select.value = chosen;
    select.dataset.platform = chosen;
    select.setAttribute("data-platform", chosen);
  }
  if (mark) {
    const icon = PLATFORM_ICONS[chosen];
    if (icon) {
      mark.hidden = false;
      mark.src = icon;
    } else {
      mark.hidden = true;
      mark.removeAttribute("src");
    }
  }
}

function markWatchPlatform(platform) {
  const chosen = WATCH_PLATFORMS.includes(platform) ? platform : "";
  markSourceSelect($("watch-platforms"), $("watch-source-mark"), chosen);
  markSourceSelect($("utility-source"), $("utility-source-mark"), chosen);
  try {
    if (chosen) sessionStorage.setItem(WATCH_PLATFORM_KEY, chosen);
    else sessionStorage.removeItem(WATCH_PLATFORM_KEY);
  } catch (_error) {
    /* The panel still works if the window cannot store the choice. */
  }
}

function watchPlayerUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch (_error) {
    return "";
  }
  if (url.protocol !== "https:" || url.username || url.password || url.port) return "";
  if (url.search || url.hash) return "";
  const host = url.hostname.toLowerCase();
  if (host === "www.youtube.com" || host === "www.youtube-nocookie.com") {
    const match = url.pathname.match(/^\/embed\/([A-Za-z0-9_-]{11})$/);
    if (!match) return "";
    return url.origin + "/embed/" + match[1];
  }
  if (host === "rumble.com") {
    const match = url.pathname.match(/^\/embed\/(v[0-9a-z]{4,16})\/$/);
    if (!match) return "";
    return "https://rumble.com/embed/" + match[1] + "/";
  }
  if (host === "player.kick.com") {
    const match = url.pathname.match(/^\/([A-Za-z0-9_-]{1,50})$/);
    if (!match) return "";
    const slug = match[1].toLowerCase();
    if (WATCH_RESERVED.includes(slug)) return "";
    return "https://player.kick.com/" + slug;
  }
  return "";
}

// The server returns a bare player URL. These are the only query fields added,
// and only after watchPlayerUrl has accepted that URL.
function watchPlaybackUrl(value, autoplay, muted) {
  const safe = watchPlayerUrl(value);
  if (!safe) return "";
  const url = new URL(safe);
  const host = url.hostname.toLowerCase();
  if (host === "www.youtube.com" || host === "www.youtube-nocookie.com") {
    url.searchParams.set("enablejsapi", "1");
    url.searchParams.set("playsinline", "1");
    url.searchParams.set("origin", location.origin);
    if (autoplay) url.searchParams.set("autoplay", "1");
    if (muted) url.searchParams.set("mute", "1");
    return url.toString();
  }
  if (host === "player.kick.com") {
    url.searchParams.set("autoplay", autoplay ? "true" : "false");
    url.searchParams.set("muted", muted ? "true" : "false");
    return url.toString();
  }
  if (host === "rumble.com") return safe;
  return "";
}

function setWatchControls(enabled) {
  ["watch-play", "watch-live", "watch-mute", "utility-play", "utility-live", "utility-mute"].forEach((id) => {
    const button = $(id);
    if (button) button.disabled = !enabled;
  });
}

function setPressed(ids, pressed) {
  const value = pressed ? "true" : "false";
  ids.forEach((id) => {
    const button = $(id);
    if (button) button.setAttribute("aria-pressed", value);
  });
}

function setWatchPlaying(playing) {
  watchPlaying = Boolean(playing);
  setPressed(["watch-play", "utility-play"], watchPlaying);
}

function setWatchMuted(muted) {
  const next = Boolean(muted);
  const button = $("watch-mute");
  if (next === watchMuted && button && button.getAttribute("aria-pressed") === (next ? "true" : "false")) {
    setPressed(["utility-mute"], next);
    return;
  }
  watchMuted = next;
  setPressed(["watch-mute", "utility-mute"], next);
  try {
    sessionStorage.setItem(WATCH_MUTED_KEY, next ? "1" : "0");
  } catch (_error) {
    /* Mute still applies to this player. */
  }
}

function utilityOn() {
  const shell = document.querySelector(".app-shell");
  return Boolean(shell && shell.classList.contains("is-utility"));
}

function activeWatchFrame() {
  return $(utilityOn() ? "utility-frame" : "watch-frame");
}

function blankWatchFrame(frame) {
  if (!frame) return;
  frame.hidden = true;
  frame.removeAttribute("src");
}

function watchSurfaceOpen() {
  if (utilityOn()) return true;
  const panel = $("watch-panel");
  return Boolean(panel && !panel.classList.contains("is-collapsed"));
}

function postYouTubeCommand(action) {
  const frame = activeWatchFrame();
  if (!frame || !frame.contentWindow || frame.hidden) return false;
  let url;
  try {
    url = new URL(frame.src);
  } catch (_error) {
    return false;
  }
  const host = url.hostname.toLowerCase();
  if (host !== "www.youtube.com" && host !== "www.youtube-nocookie.com") return false;
  const target = url.origin;
  const send = (payload) => {
    frame.contentWindow.postMessage(JSON.stringify(payload), target);
  };
  send({ event: "listening", id: "watch-frame", channel: "widget" });
  const command = { event: "command", id: "watch-frame", channel: "widget", args: [] };
  if (action === "play") command.func = "playVideo";
  else if (action === "pause") command.func = "pauseVideo";
  else if (action === "mute") command.func = "mute";
  else if (action === "unmute") command.func = "unMute";
  else if (action === "live") {
    send(Object.assign({}, command, { func: "seekTo", args: [1000000000, true] }));
    send(Object.assign({}, command, { func: "playVideo", args: [] }));
    return true;
  } else {
    return false;
  }
  send(command);
  return true;
}

function commandPlayer(action) {
  if (postYouTubeCommand(action)) return Promise.resolve("posted");
  const bridge = window.streamDonationsPlayer;
  if (!bridge || typeof bridge.command !== "function") return Promise.resolve("unavailable");
  return Promise.resolve(bridge.command(action))
    .then((result) => {
      if (result === "playing" || result === "paused" || result === "muted" || result === "unmuted" || result === "live") {
        return result;
      }
      return "unavailable";
    })
    .catch(() => "unavailable");
}

function nudgeWatchPlayback(serial) {
  [400, 1200, 2500].forEach((delay) => {
    window.setTimeout(() => {
      if (serial !== watchSerial || !watchPlaying) return;
      const frame = activeWatchFrame();
      if (!frame || frame.hidden) return;
      commandPlayer(watchMuted ? "mute" : "unmute");
      commandPlayer("play");
    }, delay);
  });
}

function showWatchFrame(bare, autoplay, force, serial) {
  const frame = activeWatchFrame();
  const src = watchPlaybackUrl(bare, autoplay, watchMuted);
  if (!frame || !src) return false;
  blankWatchFrame(frame.id === "utility-frame" ? $("watch-frame") : $("utility-frame"));
  watchBareUrl = watchPlayerUrl(bare);
  const changed = Boolean(force) || watchFrameSrc !== src;
  if (changed) {
    if (force) frame.removeAttribute("src");
    frame.src = src;
    watchFrameSrc = src;
  }
  frame.hidden = false;
  setWatchControls(true);
  if (autoplay && changed) {
    setWatchPlaying(true);
    nudgeWatchPlayback(serial);
  }
  return true;
}

function reloadWatchPlayer() {
  const platform = selectedWatchPlatform();
  if (platform) loadWatch(platform, true);
}

function onYouTubePlayerMessage(event) {
  if (!event || (event.origin !== "https://www.youtube.com" && event.origin !== "https://www.youtube-nocookie.com")) {
    return;
  }
  let data = event.data;
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch (_error) {
      return;
    }
  }
  if (!data || data.event !== "infoDelivery" || !data.info) return;
  const info = data.info;
  if (info.playerState === 1) setWatchPlaying(true);
  else if (info.playerState === 2 || info.playerState === 0) setWatchPlaying(false);
  if (typeof info.muted === "boolean") setWatchMuted(info.muted);
}

function stopWatchFrame() {
  watchSerial += 1;
  watchBareUrl = "";
  watchFrameSrc = "";
  setWatchPlaying(false);
  setWatchControls(false);
  blankWatchFrame($("watch-frame"));
  blankWatchFrame($("utility-frame"));
}

function setWatchNote(message) {
  const text = message || "";
  const note = $("watch-note");
  if (note) note.textContent = text;
  const utility = $("utility-note");
  if (utility) utility.textContent = text;
}

function setWatchChatNote(message) {
  const note = $("watch-chat-note");
  if (note) note.textContent = message || "";
}

function startWatchChat() {
  if (watchChatTimer) return;
  const tick = () => {
    if (document.hidden) return;
    loadWatchChat();
  };
  tick();
  watchChatTimer = window.setInterval(tick, 2000);
}

function stopWatchChat() {
  if (!watchChatTimer) return;
  window.clearInterval(watchChatTimer);
  watchChatTimer = 0;
}

function watchTimesOn() {
  try {
    return sessionStorage.getItem(WATCH_TIMES_KEY) !== "0";
  } catch (_error) {
    return true;
  }
}

function setWatchTimes(on) {
  const list = $("watch-chat-lines");
  const button = $("watch-chat-times");
  if (list) list.classList.toggle("is-times", on);
  if (button) button.setAttribute("aria-pressed", on ? "true" : "false");
  try {
    sessionStorage.setItem(WATCH_TIMES_KEY, on ? "1" : "0");
  } catch (_error) {
    /* The clocks still show or hide for this view. */
  }
}

function nextWatchChatRow(list, line) {
  const stamp = line && line.time ? String(line.time) : "";
  const id = Number.isInteger(line && line.id) ? line.id : null;
  if (!stamp) return null;
  for (const child of list.children) {
    const childTime = child.dataset.time || "";
    if (!childTime || stamp > childTime) continue;
    if (stamp < childTime) return child;
    const childId = Number(child.dataset.id);
    if (id !== null && Number.isInteger(childId) && id < childId) return child;
  }
  return null;
}

function holdWatchChatPlace(scroller, item) {
  if (!scroller || !item) return;
  const viewTop = scroller.getBoundingClientRect().top;
  if (item.getBoundingClientRect().bottom > viewTop + 1) return;
  const next = item.nextElementSibling;
  const shift = next
    ? next.getBoundingClientRect().top - item.getBoundingClientRect().top
    : item.getBoundingClientRect().height;
  if (shift > 0) scroller.scrollTop += shift;
}

// Custom YouTube emoji pictures. The collector checks these too, and the
// page's img-src allows only these hosts.
const CHAT_EMOJI_HOSTS = ["yt3.ggpht.com", "yt4.ggpht.com"];

function chatEmojiSrc(value) {
  let url;
  try {
    url = new URL(String(value || ""));
  } catch (_error) {
    return "";
  }
  if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) return "";
  const host = url.hostname.toLowerCase();
  return CHAT_EMOJI_HOSTS.includes(host) ? url.href : "";
}

function fillWatchChatText(node, line) {
  const parts = Array.isArray(line.parts) ? line.parts : null;
  if (!parts || !parts.length) {
    node.textContent = line.text;
    return;
  }
  parts.forEach((part) => {
    if (!part || typeof part !== "object") return;
    const shortcut = typeof part.emoji === "string" ? part.emoji : "";
    const src = shortcut ? chatEmojiSrc(part.src) : "";
    if (src) {
      const image = document.createElement("img");
      image.className = "watch-chat-emoji";
      image.alt = shortcut;
      image.title = shortcut;
      image.loading = "lazy";
      image.decoding = "async";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => image.replaceWith(document.createTextNode(shortcut)), { once: true });
      image.src = src;
      node.appendChild(image);
      return;
    }
    const words = shortcut || (typeof part.text === "string" ? part.text : "");
    if (words) node.appendChild(document.createTextNode(words));
  });
  if (!node.childNodes.length) node.textContent = line.text;
}

function appendWatchChatLine(list, line) {
  const platform = line && line.platform;
  const icon = WATCH_CHAT_ICONS[platform];
  if (!icon || !line.text) return null;
  const item = document.createElement("li");
  item.dataset.time = line.time ? String(line.time) : "";
  item.dataset.id = Number.isInteger(line.id) ? String(line.id) : "";
  const mark = document.createElement("img");
  mark.className = "watch-chat-mark";
  mark.src = icon;
  mark.alt = WATCH_CHAT_LABELS[platform];
  mark.title = WATCH_CHAT_LABELS[platform];
  const when = document.createElement("time");
  when.className = "watch-chat-time";
  const parts = localParts(line.time);
  when.dateTime = parts.time === "—" ? "" : String(line.time || "");
  when.textContent = parts.time === "—" ? "" : parts.time;
  if (line.time) when.title = line.time;
  const body = document.createElement("span");
  const author = document.createElement("span");
  author.className = "watch-chat-author";
  author.textContent = line.author || "Someone";
  const text = document.createElement("span");
  text.className = "watch-chat-text";
  fillWatchChatText(text, line);
  body.append(author, text);
  item.append(mark, when, body);
  const before = nextWatchChatRow(list, line);
  if (before) list.insertBefore(item, before);
  else list.appendChild(item);
  return item;
}

function renderWatchChat(result) {
  const list = $("watch-chat-lines");
  const scroller = $("watch-chat-scroll");
  if (!list || !result) return;
  const generation = Number.isInteger(result.generation) ? result.generation : watchChatGeneration;
  const replace = generation !== watchChatGeneration;
  const nearBottom = !scroller
    || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48;
  if (replace) {
    list.replaceChildren();
    watchChatGeneration = generation;
  }
  const lines = Array.isArray(result.lines) ? result.lines : [];
  const followTail = replace || nearBottom;
  lines.forEach((line) => {
    const item = appendWatchChatLine(list, line);
    if (!followTail) holdWatchChatPlace(scroller, item);
  });
  while (list.children.length > 200) list.removeChild(list.firstChild);
  if (Number.isInteger(result.cursor)) watchChatCursor = result.cursor;
  if (scroller && followTail) scroller.scrollTop = scroller.scrollHeight;
  if (result.paused && list.children.length === 0) {
    setWatchChatNote("Chat is paused with this ledger.");
  } else if (list.children.length === 0) {
    setWatchChatNote("Chat from the open ledger shows here.");
  } else {
    setWatchChatNote("");
  }
}

function loadWatchChat() {
  if (watchChatLoading) return;
  const panel = $("watch-panel");
  if (!panel || panel.classList.contains("is-collapsed") || utilityOn() || document.hidden) return;
  watchChatLoading = true;
  const generation = watchChatGeneration < 0 ? 0 : watchChatGeneration;
  const path = "/api/chat?after=" + encodeURIComponent(String(watchChatCursor))
    + "&generation=" + encodeURIComponent(String(generation));
  api(path)
    .then((result) => {
      const still = $("watch-panel");
      if (!still || still.classList.contains("is-collapsed") || utilityOn()) return;
      renderWatchChat(result);
    })
    .catch(() => {
      /* The next poll retries. A missed chat line does not change the video. */
    })
    .finally(() => {
      watchChatLoading = false;
    });
}

function setWatchOpen(open) {
  const panel = $("watch-panel");
  const toggle = $("watch-toggle");
  const body = $("watch-body");
  if (!panel || !toggle || !body) return;
  panel.classList.toggle("is-collapsed", !open);
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
  toggle.setAttribute("aria-label", open ? "Hide stream" : "Show stream");
  toggle.title = open ? "Hide stream" : "Show stream";
  body.hidden = !open;
  try {
    sessionStorage.setItem(WATCH_OPEN_KEY, open ? "1" : "0");
  } catch (_error) {
    /* The panel still opens. The choice is not kept after a reload. */
  }
  if (!open) {
    if (!utilityOn()) stopWatchFrame();
    stopWatchChat();
    return;
  }
  startWatchChat();
  const platform = selectedWatchPlatform();
  if (!platform) {
    setWatchNote("Choose YouTube, Rumble, or Kick.");
    return;
  }
  loadWatch(platform);
}

function loadWatch(platform, force) {
  if (!WATCH_PLATFORMS.includes(platform)) return;
  if (!watchSurfaceOpen()) return;
  const serial = ++watchSerial;
  setWatchNote("");
  api("/api/embed?platform=" + encodeURIComponent(platform))
    .then((result) => {
      if (serial !== watchSerial) return;
      if (!watchSurfaceOpen()) return;
      const safe = watchPlayerUrl(result && result.url);
      if (!safe) {
        watchBareUrl = "";
        watchFrameSrc = "";
        setWatchPlaying(false);
        setWatchControls(false);
        blankWatchFrame($("watch-frame"));
        blankWatchFrame($("utility-frame"));
        setWatchNote((result && result.message) || "No stream video for that source.");
        return;
      }
      setWatchNote((result && result.message) || "");
      showWatchFrame(safe, true, Boolean(force), serial);
    })
    .catch((error) => {
      if (serial !== watchSerial) return;
      stopWatchFrame();
      setWatchNote(error.message || "Could not load that stream.");
    });
}

function chooseWatchPlatform(platform) {
  if (!WATCH_PLATFORMS.includes(platform)) return;
  const select = $("watch-platforms");
  const same = Boolean(select) && (select.dataset.platform || "") === platform;
  markWatchPlatform(platform);
  if (utilityOn()) {
    if (!same) stopWatchFrame();
    loadWatch(platform, true);
    return;
  }
  const panel = $("watch-panel");
  if (!panel || panel.classList.contains("is-collapsed")) {
    setWatchOpen(true);
    return;
  }
  if (!same) stopWatchFrame();
  loadWatch(platform, true);
}

const UTILITY_CHATS = [
  { platform: "youtube", partition: "persist:stream-donations-youtube", path: "/api/chat/youtube" },
  { platform: "kick", partition: "persist:stream-donations-kick", path: "/api/chat/kick" },
  { platform: "rumble", partition: "persist:stream-donations-rumble", path: "/api/chat/rumble" },
];

function renderUtility(report) {
  const source = report || {};
  const total = $("utility-total");
  if (total && $("chat-line")) total.textContent = $("chat-line").value;
  const card = $("utility-donors-card");
  if (card) card.hidden = source.show_top_donors === false;
  const line = $("utility-donor-line");
  if (line) {
    line.textContent = source.donor_bar_copy || source.donor_bar || "";
    line.title = line.textContent;
  }
  const list = $("utility-donors");
  if (!list) return;
  list.replaceChildren();
  const rows = ((source.donors && source.donors.rows) || []).slice(0, 3);
  rows.forEach((donor, index) => {
    const item = document.createElement("li");
    item.append(
      el("span", "donor-rank", String(index + 1)),
      el("span", "donor-name", donorName(donor.donor)),
      el("span", "donor-usd", formatStreamUsd(donor.usd)),
    );
    list.appendChild(item);
  });
}

function utilityUsd(row) {
  if (row.amount_usd == null || row.amount_usd === "") return "—";
  const number = Number(row.amount_usd);
  if (!Number.isFinite(number)) return "—";
  return formatStreamUsd(number);
}

function renderUtilityRows() {
  const body = $("utility-rows");
  if (!body) return;
  const scroller = body.closest(".utility-ledger");
  const kept = scroller ? scroller.scrollTop : 0;
  body.replaceChildren();
  // The open page arrives newest first. This view keeps earliest first.
  const rows = state.rows.slice().reverse();
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.dataset.platform = row.platform || "other";
    if (row.hidden) tr.classList.add("is-hidden");
    const when = localParts(row.created_at);
    const timeCell = el("td", "num", when.time);
    timeCell.title = row.created_at || "";
    const donor = donorName(row.author_name);
    const donorCell = el("td", null, donor);
    if (donor !== "—") donorCell.title = donor;
    const bits = [];
    if (when.date && when.date !== "—") bits.push(when.date);
    if (row.message) bits.push(row.message);
    if (bits.length) tr.title = bits.join(" · ");
    tr.append(
      timeCell,
      donorCell,
      el("td", "num", utilityUsd(row)),
      platformCell(row),
      el("td", null, row.kind_label || row.kind || "—"),
    );
    body.appendChild(tr);
  });
  const empty = $("utility-empty");
  if (empty) empty.hidden = rows.length !== 0;
  const tail = rows.length ? String(rows[rows.length - 1].id || "") : "";
  if (scroller) {
    if (tail !== utilityLedgerTail) scroller.scrollTop = scroller.scrollHeight;
    else scroller.scrollTop = kept;
  }
  utilityLedgerTail = tail;
}

function utilityChatSection(platform) {
  return document.querySelector('.utility-chat[data-platform="' + platform + '"]');
}

function utilityChatPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(UTILITY_CHAT_KEY) || "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    return raw;
  } catch (_error) {
    return {};
  }
}

function utilityChatZoom(value) {
  const number = Math.round(Number(value) * 10) / 10;
  if (!Number.isFinite(number)) return 1;
  if (number < UTILITY_CHAT_ZOOM_MIN) return UTILITY_CHAT_ZOOM_MIN;
  if (number > UTILITY_CHAT_ZOOM_MAX) return UTILITY_CHAT_ZOOM_MAX;
  return number;
}

function utilityChatPref(platform) {
  const item = utilityChatPrefs()[platform] || {};
  return {
    zoom: utilityChatZoom(item.zoom == null ? 1 : item.zoom),
    collapsed: item.collapsed === true,
  };
}

function utilityChatConfigured(platform) {
  const settings = state.settings || {};
  const text = (value) => String(value || "").trim();
  if (platform === "youtube") {
    return settings.youtube_follow_live
      ? text(settings.youtube_channel) !== ""
      : text(settings.youtube_watch) !== "";
  }
  if (platform === "rumble") {
    return settings.rumble_follow_live
      ? text(settings.rumble_channel) !== ""
      : text(settings.rumble_watch) !== "";
  }
  if (platform === "kick") return text(settings.kick_watch) !== "";
  return true;
}

function clearUtilityChatPeek() {
  Object.keys(utilityChatPeek).forEach((platform) => {
    utilityChatPeek[platform] = false;
  });
}

// An empty address folds the strip for this ledger. The saved choice is left alone.
function utilityChatCollapsed(platform) {
  if (!utilityChatConfigured(platform) && !utilityChatPeek[platform]) return true;
  return utilityChatPref(platform).collapsed;
}

function utilityChatFoldTitle(platform, pending) {
  const section = utilityChatSection(platform);
  const label = ((section && section.getAttribute("aria-label")) || "Chat").replace(/ chat$/, "");
  if (pending) return "New chat. Show " + label + " chat";
  if (!utilityChatConfigured(platform)) {
    return "No " + label + " chat on this ledger. Show " + label + " chat";
  }
  return "Show " + label + " chat";
}

function saveUtilityChatPref(platform, patch) {
  const all = utilityChatPrefs();
  const next = Object.assign(utilityChatPref(platform), patch);
  next.zoom = utilityChatZoom(next.zoom);
  next.collapsed = Boolean(next.collapsed);
  if (next.zoom === 1 && !next.collapsed) delete all[platform];
  else all[platform] = { zoom: next.zoom, collapsed: next.collapsed };
  try {
    if (Object.keys(all).length) localStorage.setItem(UTILITY_CHAT_KEY, JSON.stringify(all));
    else localStorage.removeItem(UTILITY_CHAT_KEY);
  } catch (_error) {
    /* The chat still changes for this window. */
  }
  applyUtilityChatChrome(platform);
}

function paintUtilityChatZoom(view, zoom) {
  if (!view || typeof view.setZoomFactor !== "function") return;
  try {
    view.setZoomFactor(zoom);
  } catch (_error) {
    /* The page applies the size once it is ready. */
  }
}

function applyUtilityChatChrome(platform) {
  const section = utilityChatSection(platform);
  if (!section) return;
  const pref = utilityChatPref(platform);
  const collapsed = utilityChatCollapsed(platform);
  section.classList.toggle("is-collapsed", collapsed);
  section.setAttribute("aria-expanded", collapsed ? "false" : "true");
  section.tabIndex = collapsed ? 0 : -1;
  const unread = section.querySelector(".utility-chat-unread");
  if (unread && !collapsed) unread.hidden = true;
  const pending = Boolean(unread && !unread.hidden);
  section.title = collapsed ? utilityChatFoldTitle(platform, pending) : "";
  const smaller = section.querySelector(".utility-chat-smaller");
  const normal = section.querySelector(".utility-chat-zoom");
  const larger = section.querySelector(".utility-chat-larger");
  if (smaller) smaller.disabled = pref.zoom <= UTILITY_CHAT_ZOOM_MIN;
  if (normal) normal.disabled = pref.zoom === 1;
  if (larger) larger.disabled = pref.zoom >= UTILITY_CHAT_ZOOM_MAX;
  const host = $("utility-chat-" + platform);
  const view = host && host.querySelector("webview");
  paintUtilityChatZoom(view, pref.zoom);
}

function stepUtilityChatZoom(platform, direction) {
  const current = utilityChatPref(platform).zoom;
  const next = utilityChatZoom(current + direction * UTILITY_CHAT_ZOOM_STEP);
  if (next === current) return;
  saveUtilityChatPref(platform, { zoom: next });
}

function resetUtilityChatZoom(platform) {
  if (utilityChatPref(platform).zoom === 1) return;
  saveUtilityChatPref(platform, { zoom: 1 });
}

function setUtilityChatCollapsed(platform, collapsed) {
  saveUtilityChatPref(platform, { collapsed: Boolean(collapsed) });
}

function restoreUtilityChat(platform) {
  if (!utilityChatConfigured(platform)) utilityChatPeek[platform] = true;
  if (utilityChatPref(platform).collapsed) setUtilityChatCollapsed(platform, false);
  else applyUtilityChatChrome(platform);
}

function utilityChatUnread(platform, on) {
  const section = utilityChatSection(platform);
  if (!section) return;
  const mark = section.querySelector(".utility-chat-unread");
  if (mark) mark.hidden = !on;
  if (!section.classList.contains("is-collapsed")) return;
  section.title = utilityChatFoldTitle(platform, on);
}

function noteUtilityChatLines(result) {
  if (!result) return;
  const generation = Number.isInteger(result.generation) ? result.generation : utilityChatGeneration;
  const fresh = generation !== utilityChatGeneration;
  if (fresh) {
    utilityChatGeneration = generation;
    UTILITY_CHATS.forEach((spec) => utilityChatUnread(spec.platform, false));
  } else {
    const lines = Array.isArray(result.lines) ? result.lines : [];
    lines.forEach((line) => {
      if (!line || !line.text) return;
      const section = utilityChatSection(line.platform);
      if (!section || !section.classList.contains("is-collapsed")) return;
      utilityChatUnread(line.platform, true);
    });
  }
  if (Number.isInteger(result.cursor)) utilityChatCursor = result.cursor;
}

function loadUtilityChatWatch() {
  if (utilityChatWatchLoading || !utilityOn() || document.hidden) return;
  utilityChatWatchLoading = true;
  const generation = utilityChatGeneration < 0 ? 0 : utilityChatGeneration;
  const path = "/api/chat?after=" + encodeURIComponent(String(utilityChatCursor))
    + "&generation=" + encodeURIComponent(String(generation));
  api(path)
    .then((result) => {
      if (!utilityOn()) return;
      noteUtilityChatLines(result);
    })
    .catch(() => {
      /* The next poll retries. A missed line does not change the chat page. */
    })
    .finally(() => {
      utilityChatWatchLoading = false;
    });
}

function startUtilityChatWatch() {
  if (utilityChatWatchTimer) return;
  const tick = () => {
    if (document.hidden) return;
    loadUtilityChatWatch();
  };
  tick();
  utilityChatWatchTimer = window.setInterval(tick, 2000);
}

function stopUtilityChatWatch() {
  if (utilityChatWatchTimer) {
    window.clearInterval(utilityChatWatchTimer);
    utilityChatWatchTimer = 0;
  }
  utilityChatWatchLoading = false;
  utilityChatCursor = 0;
  utilityChatGeneration = -1;
  UTILITY_CHATS.forEach((spec) => utilityChatUnread(spec.platform, false));
}

function setUtilityChatNote(platform, message) {
  const note = $("utility-chat-" + platform + "-note");
  if (note) note.textContent = message || "";
}

function clearUtilityChat(platform) {
  const host = $("utility-chat-" + platform);
  if (host) host.replaceChildren();
  utilityChatUrls[platform] = "";
}

function mountUtilityChat(platform, partition, url) {
  const host = $("utility-chat-" + platform);
  if (!host || !url) return;
  const current = host.querySelector("webview");
  if (utilityChatUrls[platform] === url && current && current.getAttribute("src") === url) return;
  utilityChatUrls[platform] = url;
  host.replaceChildren();
  if (!window.desktop) {
    setUtilityChatNote(platform, EMBEDDED_CHAT_NEEDS_DESKTOP);
    return;
  }
  const view = document.createElement("webview");
  view.setAttribute("partition", partition);
  // The chat page belongs to the platform, so text size is the guest zoom.
  view.addEventListener("dom-ready", () => {
    paintUtilityChatZoom(view, utilityChatPref(platform).zoom);
  });
  view.src = url;
  host.appendChild(view);
  setUtilityChatNote(platform, "");
}

function openUtilityChat(spec, serial) {
  api(spec.path)
    .then((result) => {
      if (serial !== utilityChatSerial || !utilityOn()) return;
      const url = result && result.url;
      if (!url) {
        clearUtilityChat(spec.platform);
        setUtilityChatNote(spec.platform, "That chat could not be opened.");
        return;
      }
      mountUtilityChat(spec.platform, spec.partition, url);
      setUtilityChatNote(spec.platform, window.desktop ? "" : EMBEDDED_CHAT_NEEDS_DESKTOP);
    })
    .catch((error) => {
      if (serial !== utilityChatSerial || !utilityOn()) return;
      clearUtilityChat(spec.platform);
      setUtilityChatNote(spec.platform, (error && error.message) || "That chat could not be opened.");
    });
}

function refreshUtilityChat(platform) {
  const host = $("utility-chat-" + platform);
  const view = host && host.querySelector("webview");
  if (view && typeof view.reload === "function") {
    try {
      view.reload();
      return;
    } catch (_error) {
      /* Open the chat address again. */
    }
  }
  const spec = UTILITY_CHATS.find((item) => item.platform === platform);
  if (!spec || !utilityOn()) return;
  utilityChatUrls[platform] = "";
  if (view) view.remove();
  openUtilityChat(spec, utilityChatSerial);
}

function syncUtilityChats() {
  if (!utilityOn()) return;
  clearUtilityChatPeek();
  UTILITY_CHATS.forEach((spec) => applyUtilityChatChrome(spec.platform));
  const serial = ++utilityChatSerial;
  UTILITY_CHATS.forEach((spec) => openUtilityChat(spec, serial));
}

function stopUtilityChats() {
  utilityChatSerial += 1;
  UTILITY_CHATS.forEach((spec) => {
    clearUtilityChat(spec.platform);
    setUtilityChatNote(spec.platform, "");
  });
}

function setUtility(on) {
  const shell = document.querySelector(".app-shell");
  const view = $("utility-view");
  const toggle = $("utility-toggle");
  if (!shell || !view || !toggle) return;
  shell.classList.toggle("is-utility", Boolean(on));
  view.hidden = !on;
  toggle.setAttribute("aria-pressed", on ? "true" : "false");
  try {
    if (on) sessionStorage.setItem(UTILITY_KEY, "1");
    else sessionStorage.removeItem(UTILITY_KEY);
  } catch (_error) {
    /* The view still switches for this window. */
  }
  if (!on) {
    stopUtilityChats();
    stopUtilityChatWatch();
    utilityLedgerId = "";
    blankWatchFrame($("utility-frame"));
    const watchToggle = $("watch-toggle");
    const watchOpen = watchToggle && watchToggle.getAttribute("aria-expanded") === "true";
    const platform = selectedWatchPlatform();
    if (watchOpen && platform) loadWatch(platform, true);
    else stopWatchFrame();
    return;
  }
  blankWatchFrame($("watch-frame"));
  const report = state.stream;
  utilityLedgerId = (report && report.streams && report.streams.active_id) || "";
  renderUtility(report);
  utilityLedgerTail = "";
  renderUtilityRows();
  syncUtilityChats();
  startUtilityChatWatch();
  const platform = selectedWatchPlatform();
  if (!platform) {
    stopWatchFrame();
    setWatchNote("Choose YouTube, Rumble, or Kick.");
    return;
  }
  loadWatch(platform, true);
}

function bindUtilityChats() {
  document.querySelectorAll(".utility-chat").forEach((section) => {
    const platform = section.dataset.platform || "";
    if (!UTILITY_CHATS.some((spec) => spec.platform === platform)) return;
    const reload = section.querySelector(".utility-chat-reload");
    if (reload) reload.addEventListener("click", () => refreshUtilityChat(platform));
    const smaller = section.querySelector(".utility-chat-smaller");
    if (smaller) smaller.addEventListener("click", () => stepUtilityChatZoom(platform, -1));
    const normal = section.querySelector(".utility-chat-zoom");
    if (normal) normal.addEventListener("click", () => resetUtilityChatZoom(platform));
    const larger = section.querySelector(".utility-chat-larger");
    if (larger) larger.addEventListener("click", () => stepUtilityChatZoom(platform, 1));
    const collapse = section.querySelector(".utility-chat-collapse");
    if (collapse) collapse.addEventListener("click", () => setUtilityChatCollapsed(platform, true));
    section.addEventListener("click", (event) => {
      if (!section.classList.contains("is-collapsed")) return;
      if (event.target.closest(".utility-chat-tools")) return;
      restoreUtilityChat(platform);
    });
    section.addEventListener("keydown", (event) => {
      if (!section.classList.contains("is-collapsed")) return;
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      restoreUtilityChat(platform);
    });
    applyUtilityChatChrome(platform);
  });
}

// The dialog lives outside .app-shell. Kiosk view hides #manual-entry only while that form stays in the shell, so the dialog borrows the same form and this puts it back.
let kioskManualHome = null;

function openKioskManual() {
  const dialog = $("kiosk-manual");
  const form = $("manual-entry");
  const host = $("kiosk-manual-host");
  if (!dialog || !form || !host || typeof dialog.showModal !== "function") return;
  closeMenu();
  document.querySelectorAll("dialog.sheet").forEach((other) => {
    if (other !== dialog && other.open) other.close();
  });
  if (!kioskManualHome) {
    kioskManualHome = {
      parent: form.parentElement,
      next: form.nextSibling,
      collapsed: form.classList.contains("is-collapsed"),
    };
  }
  host.appendChild(form);
  const toggle = form.querySelector(".section-toggle");
  if (toggle) setSectionExpanded(toggle, true);
  if (!dialog.open) dialog.showModal();
  const note = $("toast");
  if (note) dialog.appendChild(note);
  const opener = $("kiosk-manual-open");
  if (opener) opener.setAttribute("aria-expanded", "true");
  const donor = $("manual-donor");
  if (donor) donor.focus();
}

function restoreKioskManual() {
  const form = $("manual-entry");
  const home = kioskManualHome;
  const opener = $("kiosk-manual-open");
  if (opener) opener.setAttribute("aria-expanded", "false");
  if (!form || !home || !home.parent) return;
  kioskManualHome = null;
  const toggle = form.querySelector(".section-toggle");
  if (toggle) setSectionExpanded(toggle, !home.collapsed);
  if (home.next && home.next.parentNode === home.parent) home.parent.insertBefore(form, home.next);
  else home.parent.appendChild(form);
}

function bindUtility() {
  bindUtilityChats();
  const toggle = $("utility-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      setUtility(toggle.getAttribute("aria-pressed") !== "true");
    });
  }
  const source = $("utility-source");
  if (source) {
    source.addEventListener("change", () => {
      const platform = source.value;
      if (!WATCH_PLATFORMS.includes(platform)) {
        markWatchPlatform("");
        stopWatchFrame();
        setWatchNote("Choose YouTube, Rumble, or Kick.");
        return;
      }
      chooseWatchPlatform(platform);
    });
  }
  const reload = $("utility-reload");
  if (reload) {
    reload.addEventListener("click", () => {
      const selected = selectedWatchPlatform();
      if (!selected) {
        setWatchNote("Choose YouTube, Rumble, or Kick.");
        return;
      }
      loadWatch(selected, true);
    });
  }
  const copyTotal = $("utility-copy-total");
  if (copyTotal) {
    copyTotal.addEventListener("click", () => copyText($("chat-line").value, "Stream total copied."));
  }
  const copyDonors = $("utility-copy-donors");
  if (copyDonors) {
    copyDonors.addEventListener("click", () => copyText(topDonorCopyText(), "Top donors copied."));
  }
  const sendTotal = $("utility-send-total");
  if (sendTotal) sendTotal.addEventListener("click", () => sendChatText($("chat-line").value));
  const sendDonors = $("utility-send-donors");
  if (sendDonors) sendDonors.addEventListener("click", () => sendChatText(topDonorCopyText()));
  const kioskCompose = $("kiosk-chat-compose");
  if (kioskCompose) {
    kioskCompose.addEventListener("submit", (event) => {
      event.preventDefault();
      const box = $("kiosk-chat-line");
      sendChatText(box ? box.value : "", { fromComposer: true, composer: box });
    });
  }
  const kioskManual = $("kiosk-manual");
  if (kioskManual) kioskManual.addEventListener("close", restoreKioskManual);
  const kioskManualOpen = $("kiosk-manual-open");
  if (kioskManualOpen) kioskManualOpen.addEventListener("click", openKioskManual);
  let on = false;
  try {
    on = sessionStorage.getItem(UTILITY_KEY) === "1";
  } catch (_error) {
    on = false;
  }
  if (on) setUtility(true);
}

function bindWatch() {
  let open = false;
  let platform = "";
  try {
    open = sessionStorage.getItem(WATCH_OPEN_KEY) === "1";
    platform = sessionStorage.getItem(WATCH_PLATFORM_KEY) || "";
  } catch (_error) {
    open = false;
  }
  if (!WATCH_PLATFORMS.includes(platform)) platform = "";
  markWatchPlatform(platform);
  try {
    watchMuted = sessionStorage.getItem(WATCH_MUTED_KEY) === "1";
  } catch (_error) {
    watchMuted = false;
  }
  setWatchMuted(watchMuted);
  setWatchTimes(watchTimesOn());
  ["watch-play", "utility-play"].forEach((id) => {
    const play = $(id);
    if (!play) return;
    play.addEventListener("click", () => {
      if (play.disabled) return;
      const playing = play.getAttribute("aria-pressed") === "true";
      commandPlayer(playing ? "pause" : "play").then((result) => {
        if (result === "playing" || result === "paused") setWatchPlaying(result === "playing");
        else if (result === "posted") setWatchPlaying(!playing);
        else if (!playing) reloadWatchPlayer();
      });
    });
  });
  ["watch-live", "utility-live"].forEach((id) => {
    const live = $(id);
    if (!live) return;
    live.addEventListener("click", () => {
      if (live.disabled) return;
      commandPlayer("live").then((result) => {
        if (result === "live" || result === "posted") {
          setWatchPlaying(true);
          return;
        }
        reloadWatchPlayer();
      });
    });
  });
  ["watch-mute", "utility-mute"].forEach((id) => {
    const mute = $(id);
    if (!mute) return;
    mute.addEventListener("click", () => {
      if (mute.disabled) return;
      const muted = mute.getAttribute("aria-pressed") === "true";
      commandPlayer(muted ? "unmute" : "mute").then((result) => {
        if (result === "muted" || result === "unmuted") setWatchMuted(result === "muted");
        else if (result === "posted") setWatchMuted(!muted);
        else {
          setWatchMuted(!muted);
          reloadWatchPlayer();
        }
      });
    });
  });
  window.addEventListener("message", onYouTubePlayerMessage);
  const times = $("watch-chat-times");
  if (times) {
    times.addEventListener("click", () => {
      setWatchTimes(times.getAttribute("aria-pressed") !== "true");
    });
  }
  const toggle = $("watch-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      setWatchOpen(toggle.getAttribute("aria-expanded") !== "true");
    });
  }
  const source = $("watch-platforms");
  if (source) {
    source.addEventListener("change", () => {
      const platform = source.value;
      if (!WATCH_PLATFORMS.includes(platform)) {
        markWatchPlatform("");
        stopWatchFrame();
        setWatchNote("Choose YouTube, Rumble, or Kick.");
        return;
      }
      chooseWatchPlatform(platform);
    });
  }
  const reload = $("watch-reload");
  if (reload) {
    reload.addEventListener("click", () => {
      const selected = selectedWatchPlatform();
      if (!selected) {
        setWatchNote("Choose YouTube, Rumble, or Kick.");
        return;
      }
      if ($("watch-panel").classList.contains("is-collapsed")) {
        setWatchOpen(true);
        return;
      }
      loadWatch(selected, true);
    });
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (watchChatTimer) loadWatchChat();
    if (utilityChatWatchTimer) loadUtilityChatWatch();
  });
  setWatchOpen(open);
  watchBound = true;
}

function normalizeLedgerPageSize(value) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < 1 || number > 500) return PAGE_SIZE;
  return number;
}

function syncLedgerPageSize(value) {
  const size = normalizeLedgerPageSize(value);
  const select = $("ledger-page-size");
  const custom = $("ledger-page-custom");
  const customLabel = $("ledger-page-custom-label");
  const preset = size === 10 || size === 25 || size === 50 || size === 100;
  if (select) select.value = preset ? String(size) : "custom";
  if (custom) custom.value = String(size);
  if (customLabel) customLabel.hidden = preset;
  if (state.filters.limit !== size) {
    state.filters.limit = size;
    state.filters.offset = 0;
  }
}

function ledgerPageSizeFromForm() {
  const select = $("ledger-page-size");
  if (!select || select.value !== "custom") return normalizeLedgerPageSize(select ? select.value : PAGE_SIZE);
  const raw = ($("ledger-page-custom") && $("ledger-page-custom").value.trim()) || "";
  if (!/^[0-9]+$/.test(raw)) throw new Error("Ledger page size must be a whole number.");
  const number = Number(raw);
  if (number < 1 || number > 500) throw new Error("Ledger page size must be between 1 and 500.");
  return number;
}

function applySettingsToForm(settings) {
  state.settings = settings;
  $("youtube-client-id").value = settings.youtube_client_id || "";
  $("youtube-watch").value = settings.youtube_watch || "";
  $("youtube-channel").value = settings.youtube_channel || "";
  $("youtube-follow-live").checked = Boolean(settings.youtube_follow_live);
  $("youtube-chat-commands").checked = Boolean(settings.youtube_chat_commands);
  $("youtube-poll").value = settings.youtube_poll_seconds;
  $("youtube-redirect").value = settings.google_redirect_uri || "";
  $("youtube-secret").placeholder = settings.youtube_client_secret_set
    ? "Saved. Leave blank to keep it."
    : "Client secret";
  $("rumble-poll").value = settings.rumble_poll_seconds;
  $("rumble-watch").value = settings.rumble_watch || "";
  $("rumble-channel").value = settings.rumble_channel || "";
  $("rumble-follow-live").checked = Boolean(settings.rumble_follow_live);
  $("rumble-chat-commands").checked = Boolean(settings.rumble_chat_commands);
  $("rumble-url").placeholder = settings.rumble_api_url_set
    ? "Saved. Leave blank to keep it."
    : "https://rumble.com/-livestream-api/get-data?key=…";
  $("kick-watch").value = settings.kick_watch || "";
  $("kick-follow-live").checked = Boolean(settings.kick_follow_live);
  $("kick-chat-commands").checked = Boolean(settings.kick_chat_commands);
  renderCommandNotes();
  $("kick-client-id").value = settings.kick_client_id || "";
  $("kick-host").value = settings.kick_webhook_host || "127.0.0.1";
  $("kick-port").value = settings.kick_webhook_port;
  $("kick-public").value = settings.kick_public_base || "";
  $("kick-redirect").value = settings.kick_redirect_uri || "";
  $("kick-webhook").value = settings.kick_webhook_hint || "";
  $("kick-secret").placeholder = settings.kick_client_secret_set
    ? "Saved. Leave blank to keep it."
    : "Client secret";
  if ($("membership-price")) $("membership-price").value = settings.membership_price || "";
  syncLedgerPageSize(settings.ledger_page_size);
  if ($("show-top-donors")) $("show-top-donors").checked = settings.show_top_donors !== false;
  if ($("chat-extra")) $("chat-extra").value = settings.chat_extra || "";
  if (settings.fx) renderFx(settings.fx);
  const nextSignature = watchAddressSignature(settings);
  const addressesChanged = watchBound && nextSignature !== watchSignature;
  watchSignature = nextSignature;
  if (addressesChanged) {
    const platform = selectedWatchPlatform();
    const watchOpen = $("watch-toggle") && $("watch-toggle").getAttribute("aria-expanded") === "true";
    if (platform && (watchOpen || utilityOn())) loadWatch(platform);
    if (utilityOn()) syncUtilityChats();
  }
}

function renderFx(fx) {
  const node = $("fx-status");
  if (!node) return;
  if (!fx || !fx.checked_at) {
    node.textContent = fx && fx.error
      ? fx.error
      : "Exchange rates have not been downloaded yet.";
    node.dataset.state = fx && fx.error ? "error" : "";
    return;
  }
  const checked = new Date(fx.checked_at * 1000).toLocaleString();
  let text = `Last checked ${checked}. ${fx.currencies} currencies cached.`;
  if (fx.next_update_at) {
    text += ` Their table next changes ${new Date(fx.next_update_at * 1000).toLocaleString()}.`;
  }
  if (fx.error) text += ` ${fx.error}`;
  node.textContent = text;
  node.dataset.state = fx.error ? "error" : "ok";
}

async function saveSettings(body) {
  const settings = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify(body),
  });
  applySettingsToForm(settings);
  toast("Saved");
}

function showOpenUrl(input, url, opened) {
  input.hidden = false;
  input.value = url;
  if (!opened && window.desktop && window.desktop.openExternal) {
    window.desktop.openExternal(url);
  }
  toast(opened ? "Browser opened for sign-in." : "Sign-in URL is in the field below.");
}

function bind() {
  renderTierFields([]);
  $("fx-refresh").addEventListener("click", () => {
    $("fx-refresh").disabled = true;
    api("/api/rates/refresh", { method: "POST" })
      .then((result) => {
        renderFx(result);
        if (result.outcome === "fetched") toast("Exchange rates updated.");
        else if (result.outcome === "limited") toast(result.error || "Using the saved exchange rates.", true);
        else if (result.outcome === "failed") toast(result.error || "Could not update exchange rates.", true);
        else toast("Using the saved exchange rates.");
        return refresh();
      })
      .catch((error) => toast(error.message, true))
      .finally(() => {
        $("fx-refresh").disabled = false;
      });
  });
  $("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    let ledgerPageSize;
    try {
      ledgerPageSize = ledgerPageSizeFromForm();
    } catch (error) {
      toast(error.message, true);
      return;
    }
    saveSettings({
      membership_price: $("membership-price").value.trim(),
      show_top_donors: $("show-top-donors").checked,
      chat_extra: $("chat-extra").value,
      ledger_page_size: ledgerPageSize,
    })
      .then(() => refresh())
      .catch((error) => toast(error.message, true));
  });
  const pageSize = $("ledger-page-size");
  if (pageSize) {
    pageSize.addEventListener("change", () => {
      const customLabel = $("ledger-page-custom-label");
      if (customLabel) customLabel.hidden = pageSize.value !== "custom";
      if (pageSize.value === "custom") {
        const custom = $("ledger-page-custom");
        if (custom) custom.focus();
      }
    });
  }
  $("settings-import").addEventListener("click", () => {
    $("settings-import-file").click();
  });
  $("settings-import-file").addEventListener("change", () => {
    const input = $("settings-import-file");
    const file = input.files && input.files[0];
    input.value = "";
    if (!file) return;
    if (file.size > SETTINGS_FILE_MAX_BYTES) {
      toast("That settings file is too large.", true);
      return;
    }
    file.text()
      .then((text) => api("/api/settings/import", { method: "POST", body: text }))
      .then((settings) => {
        applySettingsToForm(settings);
        const ignored = (settings.settings_import && settings.settings_import.ignored) || [];
        const skipped = ignored.length
          ? ` Skipped ${ignored.length} unused setting${ignored.length === 1 ? "" : "s"}.`
          : "";
        toast(`Imported.${skipped}`);
        return refresh();
      })
      .catch((error) => toast(error.message, true));
  });
  $("copy-chat-line").addEventListener("click", () => copyText($("chat-line").value, "Stream total copied."));
  $("copy-donor-bar").addEventListener("click", () => copyText(topDonorCopyText(), "Top donors copied."));
  const sendChatLine = $("send-chat-line");
  if (sendChatLine) sendChatLine.addEventListener("click", () => sendChatText($("chat-line").value));
  const sendDonorBar = $("send-donor-bar");
  if (sendDonorBar) sendDonorBar.addEventListener("click", () => sendChatText(topDonorCopyText()));
  const statusCopyTotal = $("status-copy-total");
  if (statusCopyTotal) {
    statusCopyTotal.addEventListener("click", () => copyText($("chat-line").value, "Stream total copied."));
  }
  const statusCopyDonors = $("status-copy-donors");
  if (statusCopyDonors) {
    statusCopyDonors.addEventListener("click", () => copyText(topDonorCopyText(), "Top donors copied."));
  }
  const statusSendTotal = $("status-send-total");
  if (statusSendTotal) statusSendTotal.addEventListener("click", () => sendChatText($("chat-line").value));
  const statusSendDonors = $("status-send-donors");
  if (statusSendDonors) statusSendDonors.addEventListener("click", () => sendChatText(topDonorCopyText()));
  applyChatTargets();
  CHAT_TARGET_ORDER.forEach((platform) => {
    const button = $("chat-target-" + platform);
    if (!button) return;
    button.addEventListener("click", () => {
      if (button.disabled) return;
      const targets = readChatTargets();
      targets[platform] = button.getAttribute("aria-pressed") !== "true";
      writeChatTargets(targets);
      applyChatTargets();
    });
  });
  const compose = $("watch-chat-compose");
  if (compose) {
    compose.addEventListener("submit", (event) => {
      event.preventDefault();
      const box = $("chat-compose");
      sendChatText(box ? box.value : "", { fromComposer: true });
    });
  }
  const composeBox = $("chat-compose");
  if (composeBox) {
    composeBox.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (compose) compose.requestSubmit();
      }
    });
  }
  const youtubeSignIn = $("youtube-sign-in");
  if (youtubeSignIn) youtubeSignIn.addEventListener("click", openYoutubeSignIn);
  const youtubePopout = $("youtube-popout");
  if (youtubePopout) youtubePopout.addEventListener("click", openYoutubeChat);
  const kickSignIn = $("kick-sign-in");
  if (kickSignIn) kickSignIn.addEventListener("click", openKickSignIn);
  const kickPopout = $("kick-popout");
  if (kickPopout) kickPopout.addEventListener("click", openKickChat);
  const rumbleSignIn = $("rumble-sign-in");
  if (rumbleSignIn) rumbleSignIn.addEventListener("click", openRumbleSignIn);
  const rumblePopout = $("rumble-popout");
  if (rumblePopout) rumblePopout.addEventListener("click", openRumbleChat);
  $("manual-native").addEventListener("input", syncManualUsd);
  $("manual-currency").addEventListener("change", syncManualUsd);
  $("manual-amount").addEventListener("input", () => {
    const currency = ($("manual-currency").value || DEFAULT_CURRENCY).trim().toUpperCase();
    if (currency === DEFAULT_CURRENCY) $("manual-native").value = $("manual-amount").value;
    updateStreamPreview();
  });
  $("manual-entry").addEventListener("reset", () => {
    setManualEditing(null);
    setTimeout(() => {
      updateStreamPreview();
      syncManualPlatformMark();
    }, 0);
  });
  const manualPlatform = $("manual-platform");
  if (manualPlatform) manualPlatform.addEventListener("change", syncManualPlatformMark);
  syncManualPlatformMark();
  $("manual-entry").addEventListener("submit", (event) => {
    event.preventDefault();
    const when = $("manual-when").value;
    const currency = ($("manual-currency").value || DEFAULT_CURRENCY).trim().toUpperCase() || DEFAULT_CURRENCY;
    const native = $("manual-native").value.trim();
    const dollars = $("manual-amount").value.trim();
    const amount = native || (currency === "USD" ? dollars : "");
    if (!amount) {
      toast("Enter the amount.", true);
      return;
    }
    const body = {
      author_name: $("manual-donor").value.trim(),
      platform: $("manual-platform").value,
      kind: $("manual-kind").value,
      amount,
      currency,
      // Hidden while collapsed. The pasted currency and message are still stored.
      message: $("manual-message").value.trim(),
    };
    if (when) {
      const parsed = new Date(when);
      if (Number.isNaN(parsed.getTime())) {
        toast("That time is not valid.", true);
        return;
      }
      body.created_at = parsed.toISOString();
    }
    const editing = editingId;
    const request = editing == null ? saveManual(body) : saveManualEdit(editing, body);
    request
      .then((saved) => {
        if (!saved) return null;
        $("manual-entry").reset();
        setManualEditing(null);
        if (editing == null) state.filters.offset = 0;
        toast(editing == null ? "Added to the ledger." : "Saved.");
        return refresh(editing == null ? { scroll: true } : undefined);
      })
      .catch((error) => {
        if (editing != null && error.message === "Donation not found") {
          $("manual-entry").reset();
          setManualEditing(null);
          toast("That entry is no longer in the ledger.", true);
          return refresh();
        }
        toast(error.message, true);
      });
  });
  $("manual-paste").addEventListener("click", () => {
    const button = $("manual-paste");
    button.disabled = true;
    readClipboard()
      .then((text) => {
        if (!String(text || "").trim()) {
          toast("Clipboard is empty.", true);
          return null;
        }
        return api("/api/paste", { method: "POST", body: JSON.stringify({ text }) });
      })
      .then((parsed) => {
        if (parsed) applyPaste(parsed);
      })
      .catch((error) => toast(error.message || "Could not read the clipboard.", true))
      .finally(() => {
        button.disabled = false;
      });
  });
  bindSectionToggles();
  $("stream-tab-add").addEventListener("click", () => {
    addStream().catch((error) => toast(error.message, true));
  });
  $("stream-tabs-prev").addEventListener("click", () => scrollStreamTabs(-1));
  $("stream-tabs-next").addEventListener("click", () => scrollStreamTabs(1));
  const scroller = $("stream-tabs");
  if (scroller) scroller.addEventListener("scroll", () => closeTabMenus());
  window.addEventListener("resize", () => closeTabMenus());
  scroller.addEventListener("scroll", updateStreamBumpers, { passive: true });
  scroller.addEventListener("wheel", (event) => {
    if (scroller.scrollWidth - scroller.clientWidth <= 2) return;
    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
    if (!delta) return;
    event.preventDefault();
    scroller.scrollLeft += delta;
  }, { passive: false });
  scroller.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const tabs = [...scroller.querySelectorAll("[role=tab]")];
    const index = tabs.findIndex((tab) => tab.getAttribute("aria-selected") === "true");
    if (index < 0) return;
    const next = index + (event.key === "ArrowRight" ? 1 : -1);
    if (next < 0 || next >= tabs.length) return;
    event.preventDefault();
    tabs[next].click();
  });
  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(() => updateStreamBumpers());
    observer.observe(scroller);
  }
  window.addEventListener("resize", updateStreamBumpers);
  $("youtube-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const body = {
      youtube_client_id: $("youtube-client-id").value.trim(),
      youtube_watch: $("youtube-watch").value.trim(),
      youtube_channel: $("youtube-channel").value.trim(),
      youtube_follow_live: $("youtube-follow-live").checked,
      youtube_chat_commands: $("youtube-chat-commands").checked,
      youtube_poll_seconds: Number($("youtube-poll").value),
    };
    if ($("youtube-secret").value) body.youtube_client_secret = $("youtube-secret").value;
    $("youtube-secret").value = "";
    saveSettings(body).catch((error) => toast(error.message, true));
  });
  $("youtube-connect").addEventListener("click", () => {
    api("/api/oauth/youtube/start", { method: "POST" })
      .then((result) => showOpenUrl($("youtube-url"), result.url, result.opened))
      .catch((error) => toast(error.message, true));
  });
  $("youtube-disconnect").addEventListener("click", () => {
    api("/api/oauth/youtube/disconnect", { method: "POST" })
      .then(applySettingsToForm)
      .then(() => toast("YouTube disconnected"))
      .catch((error) => toast(error.message, true));
  });

  $("rumble-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const body = {
      rumble_poll_seconds: Number($("rumble-poll").value),
      rumble_watch: $("rumble-watch").value.trim(),
      rumble_channel: $("rumble-channel").value.trim(),
      rumble_follow_live: $("rumble-follow-live").checked,
      rumble_chat_commands: $("rumble-chat-commands").checked,
    };
    if ($("rumble-url").value) body.rumble_api_url = $("rumble-url").value.trim();
    $("rumble-url").value = "";
    saveSettings(body).catch((error) => toast(error.message, true));
  });
  $("rumble-clear").addEventListener("click", () => {
    saveSettings({ rumble_api_url: "" }).catch((error) => toast(error.message, true));
  });

  $("kick-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const body = {
      kick_watch: $("kick-watch").value.trim(),
      kick_follow_live: $("kick-follow-live").checked,
      kick_chat_commands: $("kick-chat-commands").checked,
      kick_client_id: $("kick-client-id").value.trim(),
      kick_webhook_host: $("kick-host").value.trim(),
      kick_webhook_port: Number($("kick-port").value),
      kick_public_base: $("kick-public").value.trim(),
    };
    if ($("kick-secret").value) body.kick_client_secret = $("kick-secret").value;
    $("kick-secret").value = "";
    saveSettings(body).catch((error) => toast(error.message, true));
  });
  $("kick-connect").addEventListener("click", () => {
    api("/api/oauth/kick/start", { method: "POST" })
      .then((result) => showOpenUrl($("kick-url"), result.url, result.opened))
      .catch((error) => toast(error.message, true));
  });
  $("kick-subscribe").addEventListener("click", () => {
    api("/api/kick/subscribe", { method: "POST" })
      .then(() => refresh())
      .then(() => toast("Asked Kick to subscribe to donation events."))
      .catch((error) => toast(error.message, true));
  });
  $("kick-disconnect").addEventListener("click", () => {
    api("/api/oauth/kick/disconnect", { method: "POST" })
      .then(applySettingsToForm)
      .then(() => toast("Kick disconnected"))
      .catch((error) => toast(error.message, true));
  });

  const verboseLogs = $("verbose-logs");
  if (verboseLogs) {
    verboseLogs.addEventListener("change", () => {
      const enabled = verboseLogs.checked;
      verboseLogs.dataset.pending = "1";
      api("/api/log", { method: "PUT", body: JSON.stringify({ verbose: enabled }) })
        .then((view) => {
          verboseLogs.checked = Boolean(view && view.verbose);
          if (view && view.log_file && $("log-file")) $("log-file").textContent = view.log_file;
          return refresh().catch((error) => toast(error.message, true));
        })
        .catch((error) => {
          verboseLogs.checked = !enabled;
          toast(error.message, true);
        })
        .finally(() => {
          delete verboseLogs.dataset.pending;
        });
    });
  }
  $("hide-receipts").addEventListener("change", () => {
    readFilters();
    state.filters.offset = 0;
    refresh().catch((error) => toast(error.message, true));
  });
  $("filters").addEventListener("submit", (event) => {
    event.preventDefault();
    readFilters();
    state.filters.offset = 0;
    refresh().catch((error) => toast(error.message, true));
  });
  $("search").addEventListener("input", () => {
    clearTimeout(bind.searchTimer);
    bind.searchTimer = setTimeout(() => {
      readFilters();
      state.filters.offset = 0;
      refresh().catch((error) => toast(error.message, true));
    }, 300);
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => openSheet(button.dataset.tab));
  });
  const logButton = $("log-button");
  if (logButton) logButton.addEventListener("click", () => openSheet("log"));
  const presetForm = $("preset-form");
  if (presetForm) {
    presetForm.addEventListener("submit", (event) => {
      event.preventDefault();
      savePreset();
    });
  }
  const presetApply = $("preset-apply");
  if (presetApply) presetApply.addEventListener("click", () => applyPresetToOpenLedger());
  syncPresetApply();
  const blueForm = $("blue-form");
  if (blueForm) {
    blueForm.addEventListener("submit", (event) => {
      event.preventDefault();
      saveBlue();
    });
  }
  const blueImport = $("blue-import");
  const blueFile = $("blue-import-file");
  if (blueImport && blueFile) {
    blueImport.addEventListener("click", () => blueFile.click());
    blueFile.addEventListener("change", () => {
      const file = blueFile.files && blueFile.files[0];
      blueFile.value = "";
      importBlueFile(file);
    });
  }
  const blueExport = $("blue-export");
  if (blueExport) blueExport.addEventListener("click", () => exportBlue());
  setupBlueEditor();
  document.querySelectorAll("dialog.sheet").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", () => {
      const note = $("toast");
      if (note && note.parentElement === dialog) document.body.appendChild(note);
      if (dialog.id === "tab-log" && logButton) logButton.setAttribute("aria-expanded", "false");
    });
    dialog.querySelectorAll(".sheet-close").forEach((button) => {
      button.addEventListener("click", () => dialog.close());
    });
  });
  $("app-menu-button").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenu("app-menu", "app-menu-button");
  });
  $("export-button").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenu("export-menu", "export-button");
  });
  const exportArchive = $("export-archive");
  if (exportArchive) {
    exportArchive.addEventListener("click", () => {
      archiveOpenLedger().catch((error) => toast(error.message, true));
    });
  }
  $("ledger-help-button").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenu("ledger-help", "ledger-help-button");
  });
  $("export-menu").addEventListener("click", () => closeMenu());
  document.addEventListener("click", (event) => {
    if (event.target.closest(".menu-wrap")) return;
    closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });
  const archiveOpen = $("archive-open");
  if (archiveOpen) {
    archiveOpen.addEventListener("click", () => {
      closeMenu();
      api("/api/archive")
        .then((view) => {
          renderArchive(view);
          openSheet("archive");
        })
        .catch((error) => toast(error.message, true));
    });
  }
  $("reset-ledger").addEventListener("click", () => {
    closeMenu();
    const sure = window.confirm(
      "Delete every donation on this ledger? Other ledgers stay. Platform settings and the log stay. This cannot be undone.",
    );
    if (!sure) return;
    api("/api/ledger/reset", { method: "POST" })
      .then((result) => {
        state.filters.offset = 0;
        state.selected = null;
        $("drawer").hidden = true;
        toast(`Removed ${result.removed} donation${result.removed === 1 ? "" : "s"}.`);
        return refresh();
      })
      .catch((error) => toast(error.message, true));
  });
  $("prev").addEventListener("click", () => {
    if (state.filters.offset <= 0) return;
    state.filters.offset = Math.max(0, state.filters.offset - state.filters.limit);
    refresh().catch((error) => toast(error.message, true));
  });
  $("next").addEventListener("click", () => {
    if (state.filters.offset + state.filters.limit >= state.total) return;
    state.filters.offset += state.filters.limit;
    refresh().catch((error) => toast(error.message, true));
  });
  $("drawer-close").addEventListener("click", () => {
    $("drawer").hidden = true;
    state.selected = null;
  });
  $("save-note").addEventListener("click", () => {
    if (!state.selected) return;
    api("/api/donations/" + state.selected.id, {
      method: "PATCH",
      body: JSON.stringify({ note: $("note").value }),
    })
      .then((donation) => {
        renderDrawer(donation);
        toast("Note saved");
        return refresh();
      })
      .catch((error) => toast(error.message, true));
  });
  bindWatch();
  bindUtility();
  bindGiveaway();
}

let giveawayPoll = 0;
let giveawayState = null;

function giveawayKeywordText() {
  const field = $("giveaway-keyword");
  return field ? field.value.trim().replace(/\s+/g, " ") : "";
}

function renderGiveaway(state) {
  giveawayState = state || null;
  const running = Boolean(state && state.running);
  const closed = Boolean(state && state.closed);
  const keyword = $("giveaway-keyword");
  const minutes = $("giveaway-minutes");
  const toggle = $("giveaway-toggle");
  if (keyword) keyword.disabled = running;
  if (minutes) minutes.disabled = running;
  if (running && keyword && document.activeElement !== keyword) keyword.value = state.keyword || "";
  if (running && minutes && document.activeElement !== minutes) {
    minutes.value = state.seconds ? String(Math.round(state.seconds / 60)) : "";
  }
  if (toggle) {
    toggle.textContent = running ? "Stop" : "Start";
    toggle.setAttribute("aria-label", running ? "Stop giveaway" : "Start giveaway");
  }
  const status = $("giveaway-status");
  if (status) {
    if (running && Number.isInteger(state.remaining)) {
      const m = Math.floor(state.remaining / 60);
      const s = String(state.remaining % 60).padStart(2, "0");
      status.textContent = `Listening. ${m}:${s} left.`;
    } else if (running) {
      status.textContent = "Listening until you stop it.";
    } else if (closed && !(state.winner && state.winner.author)) {
      status.textContent = "Drawing closed. No entries.";
    } else if (closed) {
      status.textContent = "Drawing closed.";
    } else {
      status.textContent = "Not started.";
    }
  }
  const counts = $("giveaway-counts");
  if (counts) {
    if (running || closed) {
      const n = Number(state.entries) || 0;
      const platforms = state.platforms || {};
      const label = closed ? (n === 1 ? "entry left" : "entries left") : (n === 1 ? "entry" : "entries");
      counts.replaceChildren(document.createTextNode(`${n} ${label}. `));
      ["youtube", "rumble", "kick"].forEach((id, index) => {
        if (index) counts.appendChild(document.createTextNode(" "));
        const bit = document.createElement("span");
        bit.className = "platform-count";
        bit.append(platformLabel(id), document.createTextNode(` ${platforms[id] || 0}`));
        counts.appendChild(bit);
      });
    } else {
      counts.textContent = "";
    }
  }
  const winner = $("giveaway-winner");
  if (winner) {
    winner.replaceChildren();
    if (state && state.winner && state.winner.author) {
      winner.append(
        document.createTextNode(`The giveaway winner is ${state.winner.author} on `),
        platformLabel(state.winner.platform),
        document.createTextNode("."),
      );
    }
  }
  const winnersLabel = $("giveaway-winners-label");
  const winners = $("giveaway-winners");
  const drawn = state && Array.isArray(state.winners) ? state.winners : [];
  if (winnersLabel) winnersLabel.textContent = drawn.length ? "Winners" : "";
  if (winners) {
    winners.replaceChildren();
    drawn.forEach((row) => {
      const item = document.createElement("li");
      const label = document.createElement("span");
      label.append(
        document.createTextNode(`${row.author || "Someone"} on `),
        platformLabel(row.platform),
      );
      const copy = giveawayCopyButton("Copy winner");
      copy.addEventListener("click", () => {
        const text = giveawayWinnerText(row);
        if (!text) return;
        copyText(text, "Winner copied.");
      });
      item.append(label, copy);
      winners.appendChild(item);
    });
  }
  const copyKeyword = $("giveaway-copy-keyword");
  if (copyKeyword) {
    const typed = giveawayKeywordText();
    copyKeyword.disabled = !(state && state.announce) && !typed;
  }
  const redraw = $("giveaway-redraw");
  if (redraw) redraw.disabled = !(closed && Number(state && state.entries) > 0);
  renderStatusEphemeral();
  ensureGiveawayPoll();
}

function giveawayTimerLive() {
  const state = giveawayState;
  return Boolean(state && state.running && Number.isInteger(state.remaining));
}

function giveawayTimerLabel() {
  const state = giveawayState;
  if (!giveawayTimerLive()) return "";
  const m = Math.floor(state.remaining / 60);
  const s = String(state.remaining % 60).padStart(2, "0");
  return `Giveaway ${m}:${s}`;
}

function ephemeralStatusItems() {
  // Later features append their own short status here. An empty list hides the slot.
  const items = [];
  const giveaway = giveawayTimerLabel();
  if (giveaway) items.push(giveaway);
  return items;
}

function renderStatusEphemeral() {
  const slot = $("status-ephemeral");
  if (!slot) return;
  slot.replaceChildren();
  ephemeralStatusItems().forEach((text) => {
    const item = document.createElement("p");
    item.className = "status-ephemeral-item";
    item.textContent = text;
    slot.appendChild(item);
  });
}

function loadGiveaway() {
  return api("/api/giveaway").then(renderGiveaway);
}

function ensureGiveawayPoll() {
  const dialog = $("tab-giveaway");
  const dialogOpen = Boolean(dialog && dialog.open);
  if (dialogOpen || giveawayTimerLive()) startGiveawayPoll();
  else stopGiveawayPoll();
}

function startGiveawayPoll() {
  if (giveawayPoll) return;
  const tick = () => {
    if (document.hidden) return;
    const dialog = $("tab-giveaway");
    const dialogOpen = Boolean(dialog && dialog.open);
    if (!dialogOpen && !giveawayTimerLive()) {
      stopGiveawayPoll();
      return;
    }
    loadGiveaway().catch(() => {});
  };
  tick();
  giveawayPoll = window.setInterval(tick, 1000);
}

function stopGiveawayPoll() {
  if (!giveawayPoll) return;
  window.clearInterval(giveawayPoll);
  giveawayPoll = 0;
}

function giveawayWinnerText(row) {
  const author = row && row.author ? String(row.author).trim() : "";
  const platform = PLATFORM_NAMES[row && row.platform] || "";
  if (!author || !platform) return "";
  return `The giveaway winner is ${author} on ${platform}.`;
}

function giveawayCopyButton(label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-button";
  button.setAttribute("aria-label", label);
  button.title = label;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("x", "9");
  rect.setAttribute("y", "9");
  rect.setAttribute("width", "11");
  rect.setAttribute("height", "11");
  rect.setAttribute("rx", "2");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M5 15V5a2 2 0 0 1 2-2h8");
  svg.append(rect, path);
  button.appendChild(svg);
  return button;
}

function giveawayKeywordAnnounce() {
  const state = giveawayState;
  if (state && state.announce && (state.running || state.closed)) return state.announce;
  const typed = giveawayKeywordText();
  return typed ? `Type ${typed} in chat to enter the giveaway.` : "";
}

function bindGiveaway() {
  const dialog = $("tab-giveaway");
  if (dialog) dialog.addEventListener("close", ensureGiveawayPoll);
  loadGiveaway().catch(() => {});
  const form = $("giveaway-form");
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const toggle = $("giveaway-toggle");
      if (toggle && toggle.textContent !== "Stop") toggle.click();
    });
  }
  const keyword = $("giveaway-keyword");
  if (keyword) {
    keyword.addEventListener("input", () => {
      const button = $("giveaway-copy-keyword");
      if (!button) return;
      const state = giveawayState;
      button.disabled = !(state && state.announce) && !giveawayKeywordText();
    });
  }
  const toggle = $("giveaway-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const running = toggle.textContent === "Stop";
      const task = running
        ? api("/api/giveaway", { method: "POST", body: JSON.stringify({ action: "stop" }) })
        : api("/api/giveaway", {
          method: "POST",
          body: JSON.stringify({
            action: "start",
            keyword: giveawayKeywordText(),
            seconds: giveawaySeconds(),
          }),
        });
      task.then(renderGiveaway).catch((error) => toast(error.message, true));
    });
  }
  const copyKeyword = $("giveaway-copy-keyword");
  if (copyKeyword) {
    copyKeyword.addEventListener("click", () => {
      const text = giveawayKeywordAnnounce();
      if (!text) {
        toast("Enter a keyword first.", true);
        return;
      }
      copyText(text, "Keyword announcement copied.");
    });
  }
  const redraw = $("giveaway-redraw");
  if (redraw) {
    redraw.addEventListener("click", () => {
      api("/api/giveaway", { method: "POST", body: JSON.stringify({ action: "redraw" }) })
        .then(renderGiveaway)
        .catch((error) => toast(error.message, true));
    });
  }
}

function giveawaySeconds() {
  const field = $("giveaway-minutes");
  const raw = field ? field.value.trim() : "";
  if (!raw) return 0;
  const minutes = Number(raw);
  if (!Number.isInteger(minutes) || minutes < 0 || minutes > 1440) {
    throw new Error("Timer must be a whole number of minutes, up to 24 hours.");
  }
  return minutes * 60;
}

function openSheet(name) {
  const dialog = document.getElementById("tab-" + name);
  if (!dialog || dialog.tagName !== "DIALOG" || typeof dialog.showModal !== "function") return;
  closeMenu();
  document.querySelectorAll("dialog.sheet").forEach((other) => {
    if (other !== dialog && other.open) other.close();
  });
  if (!dialog.open) dialog.showModal();
  const note = $("toast");
  if (note) dialog.appendChild(note);
  const logButton = $("log-button");
  if (logButton) logButton.setAttribute("aria-expanded", name === "log" ? "true" : "false");
  if (name === "streamers") {
    syncPresetApply();
    refreshBlueScripts()
      .catch(() => {
        state.blueScripts = [];
        return [];
      })
      .then(() => refreshPresets())
      .catch((error) => toast(error.message, true));
  }
  if (name === "blue") {
    loadBlueGrammar();
    fillBlueTestForm();
    paintBlue();
    refreshBlueScripts().catch((error) => toast(error.message, true));
  }
  if (name === "giveaway") startGiveawayPoll();
  const field = dialog.querySelector("input, select, textarea");
  if (field) field.focus();
}

function confirmDuplicate(error) {
  const who = error.authorName || "this donor";
  const amount = Number(error.amount);
  const money = Number.isFinite(amount)
    ? `${amount.toFixed(2)} ${error.currency || ""}`.trim()
    : "the same amount";
  return window.confirm(
    `This ledger already has a gift from ${who} for ${money} with the same message. Save this one too?`,
  );
}

function saveManual(body) {
  return api("/api/donations", { method: "POST", body: JSON.stringify(body) }).catch((error) => {
    if (!error.duplicate) throw error;
    if (!confirmDuplicate(error)) return null;
    return api("/api/donations", {
      method: "POST",
      body: JSON.stringify({ ...body, confirm_duplicate: true }),
    });
  });
}

function saveManualEdit(id, body) {
  const send = (payload) => api("/api/donations/" + id, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return send(body).catch((error) => {
    if (!error.duplicate) throw error;
    if (!confirmDuplicate(error)) return null;
    return send({ ...body, confirm_duplicate: true });
  });
}

function localDateTimeValue(iso) {
  const parts = localParts(iso);
  if (parts.date === "—" || parts.time === "—") return "";
  return parts.date + "T" + parts.time;
}

function setManualEditing(id) {
  editingId = id;
  if (id == null) editingUsd = 0;
  const editing = id != null;
  const form = $("manual-entry");
  if (form) form.classList.toggle("is-editing", editing);
  const heading = form && form.querySelector("h2");
  if (heading) heading.textContent = editing ? "Edit entry" : "Manual entry";
  const endpoint = form && form.querySelector(".endpoint");
  if (endpoint) {
    endpoint.textContent = editing
      ? "Saving updates this manual entry. An empty time keeps the stored one."
      : "A miss, or a gift from somewhere else";
  }
  const add = $("manual-add");
  if (add) {
    add.setAttribute("aria-label", editing ? "Save" : "Add");
    add.title = editing ? "Save" : "Add";
    add.replaceChildren(icon(editing ? "check" : "plus"));
  }
  const clear = $("manual-clear");
  if (clear) clear.textContent = editing ? "Cancel" : "Clear";
  document.querySelectorAll("#rows tr").forEach((row) => {
    row.classList.toggle("is-editing", editing && row.dataset.id === String(id));
  });
}

function beginEdit(row) {
  if (!row || !row.manual) return;
  const toggle = document.querySelector("#manual-entry .section-toggle");
  if (toggle) setSectionExpanded(toggle, true);
  $("manual-donor").value = String(row.author_name || "").replace(/^@+/, "").trim();
  const platform = $("manual-platform");
  if (platform) platform.value = row.platform || "other";
  syncManualPlatformMark();
  const kind = $("manual-kind");
  if (kind && Array.from(kind.options).some((option) => option.value === row.kind)) kind.value = row.kind;
  selectCurrency(row.currency || DEFAULT_CURRENCY);
  const code = ($("manual-currency").value || DEFAULT_CURRENCY).trim().toUpperCase();
  const native = row.amount_value == null || row.amount_value === "" ? "" : Number(row.amount_value).toFixed(2);
  $("manual-native").value = native;
  const usd = Number(row.amount_usd);
  $("manual-amount").value = code === "USD" ? native : (Number.isFinite(usd) ? usd.toFixed(2) : "");
  $("manual-message").value = row.message || "";
  $("manual-when").value = localDateTimeValue(row.created_at);
  editingUsd = Number.isFinite(usd) ? usd : 0;
  setManualEditing(row.id);
  const form = $("manual-entry");
  if (form) form.scrollIntoView({ block: "start" });
  const donor = $("manual-donor");
  if (donor) donor.focus();
  updateStreamPreview();
}

function readFilters() {
  state.filters.q = $("search").value.trim();
  state.filters.hideReceipts = $("hide-receipts").checked;
}

const BROWSER_NOTIFY_KEY = "stream-donations-browser-notify";
const NOTICE_POLL_MS = 4000;
let noticeAfter = null;
let noticeTimer = null;

function browserNotifySupported() {
  return typeof window.Notification === "function";
}

function browserNotifyOn() {
  return browserNotifySupported()
    && window.Notification.permission === "granted"
    && localStorage.getItem(BROWSER_NOTIFY_KEY) === "1";
}

function paintBrowserNotify() {
  const button = $("browser-notify");
  const status = $("browser-notify-status");
  if (!button || !status) return;
  status.hidden = false;
  if (!browserNotifySupported()) {
    button.disabled = true;
    status.textContent = "This browser cannot show notifications.";
    return;
  }
  if (window.Notification.permission === "denied") {
    button.disabled = true;
    status.textContent = "Notifications are blocked for this page in the browser's site settings.";
    return;
  }
  button.disabled = false;
  const on = browserNotifyOn();
  button.textContent = on ? "Turn off browser notifications" : "Turn on browser notifications";
  status.textContent = on
    ? "New automatic donations show a browser notification while this page is open. Manual entries stay quiet."
    : "This computer has no system notifications for SuperBerry, so the browser can show them instead.";
}

function pollNotices() {
  if (!browserNotifyOn()) return;
  const path = noticeAfter === null ? "/api/notices" : "/api/notices?after=" + noticeAfter;
  api(path).then((payload) => {
    const items = (payload && Array.isArray(payload.notices)) ? payload.notices : [];
    if (noticeAfter !== null) {
      items.forEach((item) => {
        try {
          new window.Notification(String(item.title || "SuperBerry"), {
            body: String(item.body || ""),
            icon: "/icons/superberry.png",
            tag: "superberry-" + item.id,
          });
        } catch (_error) {
          // A browser that refuses page notifications still keeps the ledger.
        }
      });
    }
    if (payload && Number.isInteger(payload.last)) noticeAfter = payload.last;
  }).catch(() => {});
}

function startNoticePolling() {
  if (noticeTimer !== null) return;
  pollNotices();
  noticeTimer = window.setInterval(pollNotices, NOTICE_POLL_MS);
}

function toggleBrowserNotify() {
  if (!browserNotifySupported()) return;
  if (browserNotifyOn()) {
    localStorage.removeItem(BROWSER_NOTIFY_KEY);
    paintBrowserNotify();
    return;
  }
  // Asked only from this click, as browsers expect.
  Promise.resolve(window.Notification.requestPermission()).then((permission) => {
    if (permission === "granted") {
      localStorage.setItem(BROWSER_NOTIFY_KEY, "1");
      noticeAfter = null;
      startNoticePolling();
    }
    paintBrowserNotify();
  }).catch(() => paintBrowserNotify());
}

function showStopped() {
  appStopped = true;
  if (noticeTimer !== null) window.clearInterval(noticeTimer);
  const box = document.createElement("main");
  box.className = "oauth-result";
  const title = document.createElement("h1");
  title.textContent = "SuperBerry has stopped";
  const text = document.createElement("p");
  text.textContent = "Collection is off and the ledger is closed. You can close this tab. Start SuperBerry again to keep collecting.";
  box.append(title, text);
  document.body.replaceChildren(box);
  document.title = "SuperBerry has stopped";
}

function quitFromBrowser() {
  closeMenu();
  const sure = window.confirm("Quit SuperBerry? Collection stops until you start it again.");
  if (!sure) return;
  api("/api/shutdown", { method: "POST" })
    .then(showStopped)
    .catch((error) => toast(error.message || "SuperBerry could not quit.", true));
}

// Browser mode only. The desktop app keeps its own window, menu, and notifications.
function setupBrowserMode(meta) {
  if (!browserMode()) return;
  const section = $("browser-mode");
  if (section) section.hidden = false;
  if (meta && meta.can_quit && $("quit-app")) {
    $("quit-sep").hidden = false;
    $("quit-app").hidden = false;
    $("quit-app").addEventListener("click", quitFromBrowser);
  }
  if (meta && !meta.system_notifications && $("browser-notify")) {
    $("browser-notify-row").hidden = false;
    $("browser-notify").addEventListener("click", toggleBrowserNotify);
    paintBrowserNotify();
    if (browserNotifyOn()) startNoticePolling();
  }
}

async function boot() {
  state.meta = await api("/api/meta");
  renderPlatformPills(state.meta.platforms);
  renderCurrencies(state.meta.currencies);
  $("database").textContent = state.meta.database;
  if (state.meta.log_file && $("log-file")) $("log-file").textContent = state.meta.log_file;
  applySettingsToForm(await api("/api/settings"));
  bind();
  setupBrowserMode(state.meta);
  await refresh();
  setInterval(() => {
    if (!document.hidden) {
      refresh().catch(() => {});
    }
  }, 5000);
  // Not tied to a visible window: a minimized desktop window still answers on Rumble.
  setInterval(drainCommandReplies, COMMAND_POLL_MS);
  ["youtube", "rumble", "kick"].forEach((platform) => {
    const box = $(platform + "-chat-commands");
    if (box) box.addEventListener("change", renderCommandNotes);
  });
}

boot().catch((error) => toast(error.message, true));
