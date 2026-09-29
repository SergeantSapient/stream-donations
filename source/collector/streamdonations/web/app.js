"use strict";

const state = {
  meta: null,
  settings: null,
  rows: [],
  total: 0,
  selected: null,
  filters: {
    platform: "",
    kind: "",
    processed: "",
    q: "",
    hideReceipts: true,
    offset: 0,
    limit: 100,
  },
};

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function toast(message, bad) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  node.classList.toggle("bad", Boolean(bad));
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    node.hidden = true;
  }, 4200);
}

async function api(path, options) {
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
    throw new Error((payload && payload.error) || response.statusText || "Request failed");
  }
  return payload;
}

function queryString() {
  const params = new URLSearchParams();
  const filters = state.filters;
  if (filters.platform) params.set("platform", filters.platform);
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.processed) params.set("processed", filters.processed);
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

function formatUsd(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(Number(value));
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

function fillSelect(select, items, allLabel) {
  const current = select.value;
  select.replaceChildren();
  select.appendChild(new Option(allLabel, ""));
  items.forEach((item) => select.appendChild(new Option(item.label, item.id)));
  select.value = current;
}

function renderSummary(summary) {
  const host = $("summary");
  host.replaceChildren();
  const cards = [
    ["Total donated", formatUsd(summary.total_usd)],
    ["Waiting", String(summary.unprocessed)],
    ["Stored", String(summary.total)],
  ];
  while (cards.length < 4) cards.push(["Platforms", String((summary.by_platform || []).length)]);
  cards.slice(0, 4).forEach(([label, figure]) => {
    const card = el("article");
    card.appendChild(el("p", "kicker", label));
    card.appendChild(el("p", "figure", figure));
    host.appendChild(card);
  });
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
}

function renderRows(page) {
  state.rows = page.donations || [];
  state.total = page.total || 0;
  const body = $("rows");
  body.replaceChildren();
  state.rows.forEach((row) => {
    const tr = document.createElement("tr");
    if (row.processed) tr.classList.add("is-processed");
    if (state.selected && state.selected.id === row.id) tr.classList.add("is-selected");
    tr.tabIndex = 0;
    tr.addEventListener("click", () => openDonation(row.id));
    tr.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDonation(row.id);
      }
    });
    const when = el("td", "when", formatWhen(row.created_at));
    when.title = row.created_at || "";
    tr.append(
      when,
      el("td", null, row.platform_label || row.platform),
      el("td", null, row.kind_label || row.kind),
      el("td", null, row.author_name || "—"),
      el("td", "amount", formatAmount(row)),
      el("td", "message-cell", row.message || "—"),
      el("td", null, row.processed ? "Processed" : "Waiting"),
    );
    body.appendChild(tr);
  });
  $("empty").hidden = state.rows.length !== 0;
  const start = state.total === 0 ? 0 : state.filters.offset + 1;
  const end = Math.min(state.filters.offset + state.rows.length, state.total);
  $("page-label").textContent = `${start}–${end} of ${state.total}`;
  $("prev").disabled = state.filters.offset <= 0;
  $("next").disabled = state.filters.offset + state.filters.limit >= state.total;
  const exportQuery = queryString().replace(/&?limit=\d+/, "").replace(/&?offset=\d+/, "");
  $("export-csv").href = "/api/export.csv?" + exportQuery;
  $("export-jsonl").href = "/api/export.jsonl?" + exportQuery;
}

function fact(dl, label, value) {
  dl.appendChild(el("dt", null, label));
  dl.appendChild(el("dd", null, value || "—"));
}

function renderDrawer(donation) {
  state.selected = donation;
  $("drawer").hidden = false;
  $("drawer-title").textContent = donation.kind_label || "Donation";
  const facts = $("drawer-facts");
  facts.replaceChildren();
  fact(facts, "When", donation.created_at);
  fact(facts, "Platform", donation.platform_label);
  fact(facts, "Type", donation.kind_label);
  fact(facts, "From", donation.author_name);
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
  $("toggle-processed").textContent = donation.processed ? "Mark waiting" : "Mark processed";
  $("raw").textContent = JSON.stringify(donation.raw, null, 2);
}

async function openDonation(id) {
  const donation = await api("/api/donations/" + id);
  renderDrawer(donation);
  document.querySelectorAll("tbody tr").forEach((row, index) => {
    row.classList.toggle("is-selected", state.rows[index] && state.rows[index].id === id);
  });
}

async function refresh() {
  const [summary, status, page] = await Promise.all([
    api("/api/summary"),
    api("/api/status"),
    api("/api/donations?" + queryString()),
  ]);
  renderSummary(summary);
  if (summary.fx) renderFx(summary.fx);
  renderStatus(status);
  renderRows(page);
}

function applySettingsToForm(settings) {
  state.settings = settings;
  $("youtube-client-id").value = settings.youtube_client_id || "";
  $("youtube-watch").value = settings.youtube_watch || "";
  $("youtube-poll").value = settings.youtube_poll_seconds;
  $("youtube-redirect").value = settings.google_redirect_uri || "";
  $("youtube-secret").placeholder = settings.youtube_client_secret_set
    ? "Saved. Leave blank to keep it."
    : "Client secret";
  $("rumble-poll").value = settings.rumble_poll_seconds;
  $("rumble-watch").value = settings.rumble_watch || "";
  $("rumble-url").placeholder = settings.rumble_api_url_set
    ? "Saved. Leave blank to keep it."
    : "https://rumble.com/-livestream-api/get-data?key=…";
  $("kick-watch").value = settings.kick_watch || "";
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
  if (settings.fx) renderFx(settings.fx);
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
    saveSettings({ membership_price: $("membership-price").value.trim() })
      .then(() => refresh())
      .catch((error) => toast(error.message, true));
  });
  $("youtube-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const body = {
      youtube_client_id: $("youtube-client-id").value.trim(),
      youtube_watch: $("youtube-watch").value.trim(),
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
  $("poll-now").addEventListener("click", () => {
    api("/api/poll", { method: "POST" })
      .then((result) => {
        if (result.errors && result.errors.length) toast(result.errors.join(" "), true);
        else toast("Polled.");
        return refresh();
      })
      .catch((error) => toast(error.message, true));
  });
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => showTab(button.dataset.tab));
  });
  $("reset-ledger").addEventListener("click", () => {
    const sure = window.confirm(
      "Delete every stored donation? Platform settings and the log stay. This cannot be undone.",
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
  $("mark-loaded").addEventListener("click", () => {
    const ids = state.rows.filter((row) => !row.processed).map((row) => row.id);
    if (!ids.length) {
      toast("Nothing waiting in the loaded rows.");
      return;
    }
    api("/api/donations/bulk", {
      method: "POST",
      body: JSON.stringify({ ids, processed: true }),
    })
      .then(() => refresh())
      .then(() => toast("Marked loaded rows processed."))
      .catch((error) => toast(error.message, true));
  });
  $("prev").addEventListener("click", () => {
    state.filters.offset = Math.max(0, state.filters.offset - state.filters.limit);
    refresh().catch((error) => toast(error.message, true));
  });
  $("next").addEventListener("click", () => {
    if (state.filters.offset + state.filters.limit < state.total) {
      state.filters.offset += state.filters.limit;
      refresh().catch((error) => toast(error.message, true));
    }
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
  $("toggle-processed").addEventListener("click", () => {
    if (!state.selected) return;
    api("/api/donations/" + state.selected.id, {
      method: "PATCH",
      body: JSON.stringify({ processed: !state.selected.processed }),
    })
      .then((donation) => {
        renderDrawer(donation);
        return refresh();
      })
      .catch((error) => toast(error.message, true));
  });
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((button) => {
    const selected = button.dataset.tab === name;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-selected", selected ? "true" : "false");
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.hidden = panel.id !== "tab-" + name;
  });
}

function readFilters() {
  state.filters.platform = $("platform").value;
  state.filters.kind = $("kind").value;
  state.filters.processed = $("processed").value;
  state.filters.q = $("search").value.trim();
  state.filters.hideReceipts = $("hide-receipts").checked;
}

async function boot() {
  state.meta = await api("/api/meta");
  fillSelect($("platform"), state.meta.platforms, "All");
  fillSelect($("kind"), state.meta.kinds, "All");
  $("database").textContent = state.meta.database;
  applySettingsToForm(await api("/api/settings"));
  bind();
  await refresh();
  setInterval(() => {
    if (!document.hidden) {
      refresh().catch(() => {});
    }
  }, 5000);
}

boot().catch((error) => toast(error.message, true));
