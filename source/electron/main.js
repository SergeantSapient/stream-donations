"use strict";

const { app, BrowserWindow, dialog, ipcMain, nativeImage, session, shell } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const TOKEN = crypto.randomBytes(32).toString("hex");
const PORT = process.env.STREAM_DONATIONS_PORT || "47321";

if (process.platform === "linux") {
  app.setName("stream-donations");
  app.commandLine.appendSwitch("class", "stream-donations");
}

// The embed address is looked up after the click, so Chromium would treat
// the later play() as unattended and leave the stream paused.
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");

const PLAYER_ACTIONS = new Set(["play", "pause", "mute", "unmute", "live"]);
const PLAYER_HOSTS = new Set([
  "www.youtube.com",
  "youtube.com",
  "www.youtube-nocookie.com",
  "youtube-nocookie.com",
  "rumble.com",
  "www.rumble.com",
  "player.kick.com",
]);

function playerCommandCode(action) {
  return `(function () {
    var action = ${JSON.stringify(action)};
    var videos = document.querySelectorAll("video");
    var video = null;
    var best = -1;
    var index;
    for (index = 0; index < videos.length; index += 1) {
      var item = videos[index];
      var score = 0;
      try {
        if (item.seekable && item.seekable.length) score = item.seekable.end(item.seekable.length - 1) || 0;
      } catch (error) {
        score = 0;
      }
      if (!video || score >= best) {
        video = item;
        best = score;
      }
    }
    if (!video) return "no-video";
    function settle(pending) {
      if (pending && typeof pending.catch === "function") pending.catch(function () {});
    }
    if (action === "play") {
      settle(video.play());
      return "playing";
    }
    if (action === "pause") {
      video.pause();
      return "paused";
    }
    if (action === "mute") {
      video.muted = true;
      return "muted";
    }
    if (action === "unmute") {
      video.muted = false;
      return "unmuted";
    }
    if (action === "live") {
      try {
        var ranges = video.seekable;
        if (ranges && ranges.length) {
          var end = ranges.end(ranges.length - 1);
          if (isFinite(end) && end > 0) video.currentTime = end;
        }
      } catch (error) {}
      settle(video.play());
      return "live";
    }
    return "ignored";
  })()`;
}

function collectPlayerFrames(frame, found) {
  if (!frame) return;
  found.push(frame);
  const children = frame.frames || [];
  for (let index = 0; index < children.length; index += 1) {
    collectPlayerFrames(children[index], found);
  }
}

ipcMain.handle("stream-donations-player", async (event, action) => {
  if (!PLAYER_ACTIONS.has(action)) return "ignored";
  const contents = event.sender;
  if (!contents || contents.isDestroyed()) return "no-video";
  const frames = [];
  try {
    collectPlayerFrames(contents.mainFrame, frames);
  } catch (_error) {
    return "no-video";
  }
  const code = playerCommandCode(action);
  for (let index = 0; index < frames.length; index += 1) {
    const frame = frames[index];
    let host = "";
    try {
      host = new URL(frame.url).hostname.toLowerCase();
    } catch (_error) {
      continue;
    }
    if (!PLAYER_HOSTS.has(host)) continue;
    try {
      const result = await frame.executeJavaScript(code, true);
      if (result && result !== "no-video") return result;
    } catch (_error) {
      /* Another frame of the same player may hold the video element. */
    }
  }
  return "no-video";
});

const RUMBLE_LOGIN_URL = "https://rumble.com/login.php";
const RUMBLE_PARTITION = "persist:stream-donations-rumble";
const RUMBLE_POPUP_PATH = /^\/chat\/popup\/[0-9]{1,20}$/;

let mainWindow = null;
let rumbleLoginWindow = null;
let rumbleChatWindow = null;
let rumbleChatTarget = "";

function rumbleWebPreferences() {
  return {
    partition: RUMBLE_PARTITION,
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
  };
}

function rumbleHostAllowed(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return false;
    if (parsed.port && parsed.port !== "443") return false;
    if (parsed.username || parsed.password) return false;
    const host = parsed.hostname.toLowerCase();
    return host === "rumble.com" || host === "www.rumble.com";
  } catch (_error) {
    return false;
  }
}

function rumblePopupUrl(url) {
  if (typeof url !== "string") return "";
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return "";
    if (parsed.hostname.toLowerCase() !== "rumble.com") return "";
    if (parsed.port && parsed.port !== "443") return "";
    if (parsed.username || parsed.password) return "";
    if (parsed.search || parsed.hash) return "";
    if (!RUMBLE_POPUP_PATH.test(parsed.pathname)) return "";
    return "https://rumble.com" + parsed.pathname;
  } catch (_error) {
    return "";
  }
}

function attachRumbleGuards(rumbleWindow) {
  rumbleWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (rumbleHostAllowed(url)) {
      rumbleWindow.loadURL(url).catch(() => {});
    } else if (typeof url === "string" && url.startsWith("https://")) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });
  rumbleWindow.webContents.on("will-navigate", (event, url) => {
    if (rumbleHostAllowed(url)) return;
    event.preventDefault();
  });
  rumbleWindow.on("closed", () => {
    if (rumbleLoginWindow === rumbleWindow) rumbleLoginWindow = null;
    if (rumbleChatWindow === rumbleWindow) {
      rumbleChatWindow = null;
      rumbleChatTarget = "";
    }
  });
}

function openRumbleBrowserWindow() {
  const rumbleWindow = new BrowserWindow({
    width: 480,
    height: 760,
    minWidth: 360,
    minHeight: 480,
    backgroundColor: "#131416",
    autoHideMenuBar: true,
    title: "Rumble",
    icon: windowIcon(),
    webPreferences: rumbleWebPreferences(),
  });
  attachRumbleGuards(rumbleWindow);
  return rumbleWindow;
}

function openRumbleLogin() {
  if (rumbleLoginWindow && !rumbleLoginWindow.isDestroyed()) {
    rumbleLoginWindow.focus();
    return "focused";
  }
  rumbleLoginWindow = openRumbleBrowserWindow();
  rumbleLoginWindow.loadURL(RUMBLE_LOGIN_URL);
  return "opened";
}

function openRumbleChat(url) {
  const target = rumblePopupUrl(url);
  if (!target) return "refused";
  if (rumbleChatWindow && !rumbleChatWindow.isDestroyed()) {
    rumbleChatWindow.focus();
    if (rumbleChatTarget !== target) {
      rumbleChatTarget = target;
      rumbleChatWindow.loadURL(target);
    }
    return "focused";
  }
  rumbleChatWindow = openRumbleBrowserWindow();
  rumbleChatTarget = target;
  rumbleChatWindow.loadURL(target);
  return "opened";
}

function closeRumbleWindows() {
  [rumbleLoginWindow, rumbleChatWindow].forEach((rumbleWindow) => {
    if (rumbleWindow && !rumbleWindow.isDestroyed()) rumbleWindow.close();
  });
}

const RUMBLE_CHAT_LIMIT = 2000;
const RUMBLE_FRESH_CHAT_MS = 3000;
const rumbleChatLoadedAt = new WeakMap();

function waitForFreshRumbleChat(contents) {
  const loadedAt = rumbleChatLoadedAt.get(contents);
  if (!loadedAt) return Promise.resolve();
  const remain = RUMBLE_FRESH_CHAT_MS - (Date.now() - loadedAt);
  if (remain <= 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, remain));
}

function classifyRumbleText(value) {
  if (typeof value !== "string") return { error: "refused" };
  const text = value.split(/\s+/).join(" ").trim();
  if (!text) return { error: "empty" };
  if (text.length > RUMBLE_CHAT_LIMIT) return { error: "overlong" };
  return { text };
}

function rumblePopupLoaded(url, target) {
  if (rumblePopupUrl(url) === target) return true;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return false;
    if (parsed.port && parsed.port !== "443") return false;
    if (parsed.username || parsed.password) return false;
    const host = parsed.hostname.toLowerCase();
    if (host !== "rumble.com" && host !== "www.rumble.com") return false;
    return rumblePopupUrl("https://rumble.com" + parsed.pathname) === target;
  } catch (_error) {
    return false;
  }
}

function rumbleContentsOwned(contents) {
  if (!contents || contents.isDestroyed()) return false;
  if (rumbleLoginWindow && !rumbleLoginWindow.isDestroyed() && contents.id === rumbleLoginWindow.webContents.id) {
    return false;
  }
  try {
    return contents.session === session.fromPartition(RUMBLE_PARTITION);
  } catch (_error) {
    return false;
  }
}

function matchingRumbleGuest(target) {
  const pages = require("electron").webContents.getAllWebContents();
  for (let index = 0; index < pages.length; index += 1) {
    const contents = pages[index];
    if (!rumbleContentsOwned(contents)) continue;
    if (rumbleChatWindow && !rumbleChatWindow.isDestroyed() && contents.id === rumbleChatWindow.webContents.id) {
      continue;
    }
    if (rumblePopupLoaded(contents.getURL(), target)) return contents;
  }
  return null;
}

function waitForRumblePopup(contents, target, timeoutMs) {
  if (!contents || contents.isDestroyed()) return Promise.resolve("failed");
  if (rumblePopupLoaded(contents.getURL(), target)) return Promise.resolve("ready");
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      contents.removeListener("did-finish-load", onLoad);
      contents.removeListener("did-fail-load", onFail);
      contents.removeListener("destroyed", onFail);
      resolve(value);
    };
    const timer = setTimeout(() => finish("timeout"), timeoutMs);
    const onLoad = () => {
      if (!rumblePopupLoaded(contents.getURL(), target)) return;
      rumbleChatLoadedAt.set(contents, Date.now());
      finish("ready");
    };
    const onFail = () => finish("failed");
    contents.on("did-finish-load", onLoad);
    contents.on("did-fail-load", onFail);
    contents.on("destroyed", onFail);
  });
}

function rumbleSendContents(target) {
  const popout = rumbleChatWindow && !rumbleChatWindow.isDestroyed() && rumbleChatTarget === target
    ? rumbleChatWindow
    : null;
  const popoutReady = popout ? waitForRumblePopup(popout.webContents, target, 15000) : Promise.resolve("");
  return popoutReady.then((ready) => {
    if (
      ready === "ready"
      && rumbleContentsOwned(popout.webContents)
      && rumblePopupLoaded(popout.webContents.getURL(), target)
    ) {
      return popout.webContents;
    }
    const guest = matchingRumbleGuest(target);
    if (guest) return guest;
    if (popout) return null;
    if (openRumbleChat(target) === "refused" || !rumbleChatWindow || rumbleChatWindow.isDestroyed()) return null;
    const opened = rumbleChatWindow;
    return waitForRumblePopup(opened.webContents, target, 15000).then((state) => {
      if (
        state === "ready"
        && rumbleContentsOwned(opened.webContents)
        && rumblePopupLoaded(opened.webContents.getURL(), target)
      ) {
        return opened.webContents;
      }
      return null;
    });
  });
}

function withTimeout(pending, timeoutMs, fallback) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const timer = setTimeout(() => finish(fallback), timeoutMs);
    Promise.resolve(pending).then((value) => finish(value), () => finish(fallback));
  });
}

// Fixed page script. The caller supplies the message text, not code.
function rumbleSendCode(text) {
  return `(function () {
    var message = ${JSON.stringify(text)};
    var limitMs = 8000;
    function shown(node) {
      if (!node || !node.getClientRects) return false;
      var style = window.getComputedStyle(node);
      if (!style || style.display === "none" || style.visibility === "hidden") return false;
      var rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    }
    function findForm() {
      return document.querySelector("#chat-message-form") || document.querySelector("form.chat-message-form");
    }
    function findField(form) {
      return form.querySelector("#chat-message-text-input")
        || form.querySelector("textarea, input[type='text'], [contenteditable='true']");
    }
    function same(value) {
      return String(value || "").trim() === message;
    }
    function setField(field) {
      field.focus();
      if (field.isContentEditable) {
        try {
          var selection = window.getSelection();
          var range = document.createRange();
          range.selectNodeContents(field);
          selection.removeAllRanges();
          selection.addRange(range);
        } catch (error) {}
        var wrote = false;
        try { wrote = document.execCommand("insertText", false, message); } catch (error) { wrote = false; }
        if (!wrote) field.textContent = message;
        try {
          field.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: message }));
        } catch (error) {
          field.dispatchEvent(new Event("input", { bubbles: true }));
        }
        return same(field.textContent);
      }
      var prototype = Object.getPrototypeOf(field);
      var descriptor = prototype && Object.getOwnPropertyDescriptor(prototype, "value");
      if (!descriptor || !descriptor.set) {
        var owner = field.tagName === "TEXTAREA" ? window.HTMLTextAreaElement : window.HTMLInputElement;
        descriptor = owner && Object.getOwnPropertyDescriptor(owner.prototype, "value");
      }
      if (descriptor && descriptor.set) descriptor.set.call(field, message);
      else field.value = message;
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
      return same(field.value);
    }
    function controlLabel(node) {
      var label = (node.getAttribute("aria-label") || "") + " " + (node.getAttribute("title") || "") + " " + (node.textContent || "");
      var named = node.querySelectorAll("[aria-label], [title]");
      var index;
      for (index = 0; index < named.length; index += 1) {
        label += " " + (named[index].getAttribute("aria-label") || "") + " " + (named[index].getAttribute("title") || "");
      }
      return label.toLowerCase();
    }
    function unsafeControl(node) {
      var blob = String((node.className && node.className.baseVal) || node.className || "")
        + " " + (node.id || "") + " " + controlLabel(node);
      blob = blob.toLowerCase();
      if (blob.indexOf("rant") !== -1 || blob.indexOf("emoji") !== -1 || blob.indexOf("emote") !== -1
        || blob.indexOf("gift") !== -1 || blob.indexOf("sticker") !== -1
        || blob.indexOf("subscribe") !== -1 || blob.indexOf("premium") !== -1
        || blob.indexOf("badge") !== -1 || blob.indexOf("dollar") !== -1) return true;
      var text = (node.textContent || "").replace(/\\s+/g, "");
      return text === "$" || text === "＄" || text.indexOf("$") === 0;
    }
    function subscribeOpen() {
      var nodes = document.querySelectorAll("button, a, [role='button']");
      var index;
      for (index = 0; index < nodes.length; index += 1) {
        var text = (nodes[index].textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
        if (text.indexOf("subscribe") === 0 && shown(nodes[index]) && nodes[index].getBoundingClientRect().width > 100) {
          return true;
        }
      }
      return false;
    }
    function findSubmit(form, field) {
      var fieldRect = field.getBoundingClientRect();
      var roots = [];
      function addRoot(node) {
        if (!node || roots.indexOf(node) !== -1) return;
        roots.push(node);
      }
      var cursor = form;
      var depth;
      for (depth = 0; depth < 5 && cursor; depth += 1) {
        addRoot(cursor);
        addRoot(cursor.nextElementSibling);
        cursor = cursor.parentElement;
      }
      var best = null;
      var bestRight = -1;
      var rootIndex;
      var index;
      for (rootIndex = 0; rootIndex < roots.length; rootIndex += 1) {
        var nodes = roots[rootIndex].querySelectorAll("button, a, [role='button'], svg");
        for (index = 0; index < nodes.length; index += 1) {
          var node = nodes[index];
          if (!shown(node) || node.disabled || unsafeControl(node)) continue;
          if ((node.getAttribute("aria-disabled") || "").toLowerCase() === "true") continue;
          if ((node.getAttribute("type") || "").toLowerCase() === "reset") continue;
          var rect = node.getBoundingClientRect();
          if (rect.width > 88 || rect.height > 72 || rect.width < 16 || rect.height < 16) continue;
          var cx = rect.left + (rect.width / 2);
          var cy = rect.top + (rect.height / 2);
          if (cx >= fieldRect.left && cx <= fieldRect.right && cy >= fieldRect.top && cy <= fieldRect.bottom) continue;
          if (cy < fieldRect.bottom - 4 || cy > fieldRect.bottom + 120) continue;
          if (rect.right > bestRight) {
            best = node;
            bestRight = rect.right;
          }
        }
      }
      return best;
    }
    function pointOf(node) {
      try { node.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (error) {}
      var rect = node.getBoundingClientRect();
      var x = Math.round(rect.left + (rect.width / 2));
      var y = Math.round(rect.top + (rect.height / 2));
      if (!(x >= 0) || !(y >= 0)) return "";
      return "click:" + x + ":" + y;
    }
    var started = Date.now();
    function attempt() {
      try {
        var form = findForm();
        var signedOut = document.querySelector(".chat--signin-container");
        if (!form || !shown(form)) {
          if (shown(signedOut)) return "signed-out";
          if (Date.now() - started > limitMs) return "no-composer";
          return "";
        }
        var field = findField(form);
        if (!field || field.disabled) {
          if (shown(signedOut)) return "signed-out";
          if (Date.now() - started > limitMs) return "no-composer";
          return "";
        }
        var cap = Number(field.getAttribute("maxlength") || "0");
        if (cap && message.length > cap) return "too-long";
        if (!setField(field)) return "no-composer";
        if (subscribeOpen()) return "overlay";
        field.focus();
        var button = findSubmit(form, field);
        if (button) return pointOf(button) || "enter";
        if (Date.now() - started > limitMs) return "enter";
        return "";
      } catch (error) {
        return "failed";
      }
    }
    return new Promise(function (resolve) {
      function tick() {
        var result = attempt();
        if (result) resolve(result);
        else window.setTimeout(tick, 250);
      }
      tick();
    });
  })()`;
}

function rumbleSendOutcome(result) {
  if (result === "sent" || result === "signed-out" || result === "no-composer" || result === "too-long") return result;
  return "failed";
}

function rumbleClickPoint(result) {
  if (typeof result !== "string") return null;
  const parts = result.split(":");
  if (parts.length !== 3 || parts[0] !== "click") return null;
  const x = Number(parts[1]);
  const y = Number(parts[2]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  if (x < 0 || y < 0 || x > 10000 || y > 10000) return null;
  return { x, y };
}

function withRumbleDebugger(contents, run) {
  if (!contents || contents.isDestroyed()) return Promise.resolve(false);
  const dbg = contents.debugger;
  let attachedHere = false;
  try {
    if (!dbg.isAttached()) {
      dbg.attach("1.3");
      attachedHere = true;
    }
  } catch (_error) {
    return Promise.resolve(false);
  }
  const release = () => {
    if (!attachedHere) return;
    try {
      if (dbg.isAttached()) dbg.detach();
    } catch (_error) {}
  };
  return Promise.resolve()
    .then(run)
    .then(
      (value) => {
        release();
        return value;
      },
      (error) => {
        release();
        throw error;
      },
    );
}

function clickRumbleSend(contents, point) {
  if (!point) return Promise.resolve(false);
  return withRumbleDebugger(contents, () => {
    const dbg = contents.debugger;
    const move = { type: "mouseMoved", x: point.x, y: point.y, button: "none" };
    const press = { type: "mousePressed", x: point.x, y: point.y, button: "left", clickCount: 1 };
    const release = { type: "mouseReleased", x: point.x, y: point.y, button: "left", clickCount: 1 };
    return dbg.sendCommand("Input.dispatchMouseEvent", move)
      .then(() => dbg.sendCommand("Input.dispatchMouseEvent", press))
      .then(() => dbg.sendCommand("Input.dispatchMouseEvent", release))
      .then(() => true);
  }).catch(() => false);
}

function pressRumbleKey(contents, key, code) {
  return withRumbleDebugger(contents, () => {
    const dbg = contents.debugger;
    const down = {
      type: "keyDown",
      key,
      code: key,
      windowsVirtualKeyCode: code,
      nativeVirtualKeyCode: code,
    };
    const up = {
      type: "keyUp",
      key,
      code: key,
      windowsVirtualKeyCode: code,
      nativeVirtualKeyCode: code,
    };
    return dbg.sendCommand("Input.dispatchKeyEvent", down)
      .then(() => dbg.sendCommand("Input.dispatchKeyEvent", up))
      .then(() => true);
  }).catch(() => false);
}

function pressRumbleEnter(contents) {
  return pressRumbleKey(contents, "Enter", 13);
}

function closeRumbleOverlay(contents) {
  const closeCode = `(function () {
    var nodes = document.querySelectorAll("button, [role='button']");
    var index;
    for (index = 0; index < nodes.length; index += 1) {
      var node = nodes[index];
      var style = window.getComputedStyle(node);
      if (!style || style.display === "none" || style.visibility === "hidden") continue;
      var rect = node.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0 || rect.width > 56) continue;
      var label = ((node.getAttribute("aria-label") || "") + " " + (node.getAttribute("title") || "") + " " + (node.textContent || "")).toLowerCase();
      var text = (node.textContent || "").trim();
      if (label.indexOf("subscribe") !== -1) continue;
      if (label.indexOf("close") !== -1 || text === "×" || text === "✕" || text === "x" || text === "X") {
        node.click();
        return "closed";
      }
    }
    return "open";
  })()`;
  return withTimeout(contents.executeJavaScript(closeCode, true), 2000, "open")
    .catch(() => "open")
    .then(() => pressRumbleKey(contents, "Escape", 27));
}

function rumbleFieldCleared(text) {
  return `(function () {
    var message = ${JSON.stringify(text)};
    var limitMs = 2500;
    var started = Date.now();
    function fieldValue() {
      var form = document.querySelector("#chat-message-form") || document.querySelector("form.chat-message-form");
      if (!form) return "";
      var field = form.querySelector("#chat-message-text-input, textarea, input[type='text'], [contenteditable='true']");
      if (!field) return "";
      return String(field.isContentEditable ? field.textContent : field.value || "").trim();
    }
    function subscribeOpen() {
      var nodes = document.querySelectorAll("button, a, [role='button']");
      var index;
      for (index = 0; index < nodes.length; index += 1) {
        var text = (nodes[index].textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
        var style = window.getComputedStyle(nodes[index]);
        var rect = nodes[index].getBoundingClientRect();
        if (!style || style.display === "none" || style.visibility === "hidden") continue;
        if (text.indexOf("subscribe") === 0 && rect.width > 100 && rect.height > 0) return true;
      }
      return false;
    }
    function submitProblem() {
      var nodes = document.querySelectorAll("div, p, span");
      var index;
      for (index = 0; index < nodes.length; index += 1) {
        var text = (nodes[index].textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
        if (text.length > 240 || text.indexOf("problem submitting") === -1) continue;
        var style = window.getComputedStyle(nodes[index]);
        var rect = nodes[index].getBoundingClientRect();
        if (!style || style.display === "none" || style.visibility === "hidden") continue;
        if (rect.width > 0 && rect.height > 0) return true;
      }
      return false;
    }
    return new Promise(function (resolve) {
      function tick() {
        if (submitProblem()) resolve("rejected");
        else if (subscribeOpen()) resolve("overlay");
        else if (fieldValue() !== message) resolve("sent");
        else if (Date.now() - started > limitMs) resolve("failed");
        else window.setTimeout(tick, 200);
      }
      tick();
    });
  })()`;
}

function deliverRumbleSend(contents, text, result) {
  const point = rumbleClickPoint(result);
  if (result === "overlay") return closeRumbleOverlay(contents).then(() => "failed");
  if (result !== "enter" && !point) return Promise.resolve(result);
  const finish = (cleared) => {
    if (cleared !== "overlay") return cleared;
    return closeRumbleOverlay(contents).then(() => "failed");
  };
  const check = () => withTimeout(
    contents.executeJavaScript(rumbleFieldCleared(text), true),
    4000,
    "failed",
  ).then(finish);
  const enter = () => pressRumbleEnter(contents).then((pressed) => {
    if (!pressed || !rumbleContentsOwned(contents)) return "failed";
    return check();
  });
  if (!point) return enter();
  return clickRumbleSend(contents, point).then((clicked) => {
    if (!clicked || !rumbleContentsOwned(contents)) return "failed";
    return check().then((cleared) => (cleared === "sent" || cleared === "rejected" ? cleared : enter()));
  });
}

function sendRumbleChat(text, url) {
  const classified = classifyRumbleText(text);
  if (classified.error) return Promise.resolve(classified.error);
  const target = rumblePopupUrl(typeof url === "string" ? url : "");
  if (!target) return Promise.resolve("refused");
  return rumbleSendContents(target).then((contents) => {
    if (!rumbleContentsOwned(contents) || !rumblePopupLoaded(contents.getURL(), target)) return "failed";
    const run = () => withTimeout(
      contents.executeJavaScript(rumbleSendCode(classified.text), true),
      12000,
      "failed",
    );
    const finishSend = (result) => {
      if (result !== "overlay") return deliverRumbleSend(contents, classified.text, result);
      return closeRumbleOverlay(contents).then(run).then((again) => (
        deliverRumbleSend(contents, classified.text, again === "overlay" ? "failed" : again)
      ));
    };
    const attemptSend = () => {
      if (!rumbleContentsOwned(contents) || !rumblePopupLoaded(contents.getURL(), target)) return "failed";
      return run().then(finishSend);
    };
    return waitForFreshRumbleChat(contents)
      .then(attemptSend)
      .then((outcome) => {
        if (outcome !== "rejected") return outcome;
        return new Promise((resolve) => setTimeout(resolve, 1500)).then(attemptSend);
      })
      .then(rumbleSendOutcome)
      .catch(() => "failed");
  });
}

ipcMain.handle("stream-donations-rumble", async (event, payload) => {
  if (!mainWindow || mainWindow.isDestroyed() || event.sender !== mainWindow.webContents) return "refused";
  const action = payload && payload.action;
  if (action === "login") return openRumbleLogin();
  if (action === "chat") return openRumbleChat(payload && payload.url);
  if (action === "send") return sendRumbleChat(payload && payload.text, payload && payload.url);
  return "refused";
});

const YOUTUBE_LOGIN_URL = "https://www.youtube.com/signin";
const YOUTUBE_PARTITION = "persist:stream-donations-youtube";
const YOUTUBE_HOSTS = new Set(["youtube.com", "www.youtube.com", "accounts.google.com"]);
const YOUTUBE_VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

const KICK_LOGIN_URL = "https://kick.com/login";
const KICK_PARTITION = "persist:stream-donations-kick";
const KICK_HOSTS = new Set(["kick.com", "www.kick.com", "id.kick.com"]);
const KICK_POPUP_PATH = /^\/popout\/([A-Za-z0-9_-]{1,50})\/chat$/;

function siteHostAllowed(url, hosts) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return false;
    if (parsed.port && parsed.port !== "443") return false;
    if (parsed.username || parsed.password) return false;
    return hosts.has(parsed.hostname.toLowerCase());
  } catch (_error) {
    return false;
  }
}

function attachSiteGuards(siteWindow, hosts, onClosed) {
  siteWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (siteHostAllowed(url, hosts)) {
      siteWindow.loadURL(url).catch(() => {});
    } else if (typeof url === "string" && url.startsWith("https://")) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });
  siteWindow.webContents.on("will-navigate", (event, url) => {
    if (siteHostAllowed(url, hosts)) return;
    event.preventDefault();
  });
  siteWindow.on("closed", () => onClosed(siteWindow));
}

function openSiteBrowserWindow(title, partition, hosts, onClosed) {
  const siteWindow = new BrowserWindow({
    width: 480,
    height: 760,
    minWidth: 360,
    minHeight: 480,
    backgroundColor: "#131416",
    autoHideMenuBar: true,
    title: title,
    icon: windowIcon(),
    webPreferences: {
      partition: partition,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  attachSiteGuards(siteWindow, hosts, onClosed);
  return siteWindow;
}

function createSiteWindows(spec) {
  const state = { login: null, chat: null, target: "" };

  function forget(siteWindow) {
    if (state.login === siteWindow) state.login = null;
    if (state.chat === siteWindow) {
      state.chat = null;
      state.target = "";
    }
  }

  function openBrowser() {
    return openSiteBrowserWindow(spec.title, spec.partition, spec.hosts, forget);
  }

  return {
    openLogin() {
      if (state.login && !state.login.isDestroyed()) {
        state.login.focus();
        return "focused";
      }
      state.login = openBrowser();
      state.login.loadURL(spec.loginUrl);
      return "opened";
    },
    openChat(url) {
      const target = spec.acceptChatUrl(url);
      if (!target) return "refused";
      if (state.chat && !state.chat.isDestroyed()) {
        state.chat.focus();
        if (state.target !== target) {
          state.target = target;
          state.chat.loadURL(target);
        }
        return "focused";
      }
      state.chat = openBrowser();
      state.target = target;
      state.chat.loadURL(target);
      return "opened";
    },
    close() {
      [state.login, state.chat].forEach((siteWindow) => {
        if (siteWindow && !siteWindow.isDestroyed()) siteWindow.close();
      });
    },
  };
}

function youtubePopupUrl(url) {
  if (typeof url !== "string") return "";
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return "";
    if (parsed.hostname.toLowerCase() !== "www.youtube.com") return "";
    if (parsed.port && parsed.port !== "443") return "";
    if (parsed.username || parsed.password) return "";
    if (parsed.hash) return "";
    if (parsed.pathname !== "/live_chat") return "";
    const keys = [...parsed.searchParams.keys()];
    if (keys.length !== 2 || keys[0] !== "is_popout" || keys[1] !== "v") return "";
    if (parsed.searchParams.get("is_popout") !== "1") return "";
    const videoId = parsed.searchParams.get("v") || "";
    if (!YOUTUBE_VIDEO_ID.test(videoId)) return "";
    return "https://www.youtube.com/live_chat?is_popout=1&v=" + videoId;
  } catch (_error) {
    return "";
  }
}

function kickPopupUrl(url) {
  if (typeof url !== "string") return "";
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return "";
    if (parsed.hostname.toLowerCase() !== "kick.com") return "";
    if (parsed.port && parsed.port !== "443") return "";
    if (parsed.username || parsed.password) return "";
    if (parsed.search || parsed.hash) return "";
    const match = KICK_POPUP_PATH.exec(parsed.pathname);
    if (!match) return "";
    return "https://kick.com/popout/" + match[1].toLowerCase() + "/chat";
  } catch (_error) {
    return "";
  }
}

const youtubeWindows = createSiteWindows({
  title: "YouTube",
  partition: YOUTUBE_PARTITION,
  hosts: YOUTUBE_HOSTS,
  loginUrl: YOUTUBE_LOGIN_URL,
  acceptChatUrl: youtubePopupUrl,
});

const kickWindows = createSiteWindows({
  title: "Kick",
  partition: KICK_PARTITION,
  hosts: KICK_HOSTS,
  loginUrl: KICK_LOGIN_URL,
  acceptChatUrl: kickPopupUrl,
});

function openYoutubeLogin() {
  return youtubeWindows.openLogin();
}

function openYoutubeChat(url) {
  return youtubeWindows.openChat(url);
}

function closeYoutubeWindows() {
  youtubeWindows.close();
}

function openKickLogin() {
  return kickWindows.openLogin();
}

function openKickChat(url) {
  return kickWindows.openChat(url);
}

function closeKickWindows() {
  kickWindows.close();
}

const RUMBLE_HOSTS = new Set(["rumble.com", "www.rumble.com"]);

function chatGuestTarget(partition, src) {
  if (partition === YOUTUBE_PARTITION) return youtubePopupUrl(src);
  if (partition === KICK_PARTITION) return kickPopupUrl(src);
  if (partition === RUMBLE_PARTITION) return rumblePopupUrl(src);
  return "";
}

function chatGuestHosts(guest) {
  let current = null;
  try {
    current = guest.session;
  } catch (_error) {
    return null;
  }
  if (current === session.fromPartition(YOUTUBE_PARTITION)) return YOUTUBE_HOSTS;
  if (current === session.fromPartition(KICK_PARTITION)) return KICK_HOSTS;
  if (current === session.fromPartition(RUMBLE_PARTITION)) return RUMBLE_HOSTS;
  return null;
}

function attachChatGuest(guest) {
  const hosts = chatGuestHosts(guest);
  if (!hosts) {
    try {
      guest.destroy();
    } catch (_error) {
      /* The guest is already gone. */
    }
    return;
  }
  guest.setWindowOpenHandler(({ url }) => {
    if (siteHostAllowed(url, hosts)) {
      guest.loadURL(url).catch(() => {});
    } else if (typeof url === "string" && url.startsWith("https://")) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });
  guest.on("will-navigate", (event, url) => {
    if (siteHostAllowed(url, hosts)) return;
    event.preventDefault();
  });
}

// Embedded chats use the same site partitions as the pop-out windows.
// Only a checked chat URL can attach, and the guest cannot enable Node or a preload.
function attachChatWebviews(contents) {
  contents.on("will-attach-webview", (event, webPreferences, params) => {
    const requested = String(
      (params && params.partition) || (webPreferences && webPreferences.partition) || "",
    );
    const src = chatGuestTarget(requested, params && params.src);
    delete webPreferences.preload;
    delete webPreferences.preloadURL;
    webPreferences.nodeIntegration = false;
    webPreferences.nodeIntegrationInSubFrames = false;
    webPreferences.contextIsolation = true;
    webPreferences.sandbox = true;
    if (!src) {
      event.preventDefault();
      return;
    }
    params.src = src;
    webPreferences.partition = requested;
  });
  contents.on("did-attach-webview", (_event, guest) => {
    attachChatGuest(guest);
  });
}

ipcMain.handle("stream-donations-youtube", async (event, payload) => {
  if (!mainWindow || mainWindow.isDestroyed() || event.sender !== mainWindow.webContents) return "refused";
  const action = payload && payload.action;
  if (action === "login") return openYoutubeLogin();
  if (action === "chat") return openYoutubeChat(payload && payload.url);
  return "refused";
});

ipcMain.handle("stream-donations-kick", async (event, payload) => {
  if (!mainWindow || mainWindow.isDestroyed() || event.sender !== mainWindow.webContents) return "refused";
  const action = payload && payload.action;
  if (action === "login") return openKickLogin();
  if (action === "chat") return openKickChat(payload && payload.url);
  return "refused";
});

let collector = null;
let quitting = false;
let stderrTail = "";

function collectorRoot() {
  if (process.env.STREAM_DONATIONS_COLLECTOR) {
    return process.env.STREAM_DONATIONS_COLLECTOR;
  }
  return path.resolve(__dirname, "..", "collector");
}

function defaultDataDir() {
  const home = os.homedir();
  if (process.platform === "win32") {
    const base = process.env.APPDATA || path.join(home, "AppData", "Roaming");
    return path.join(base, "stream-donations");
  }
  if (process.platform === "darwin") {
    return path.join(home, "Library", "Application Support", "stream-donations");
  }
  const base = process.env.XDG_DATA_HOME || path.join(home, ".local", "share");
  return path.join(base, "stream-donations");
}

function outsideHome(dir) {
  const home = path.resolve(os.homedir());
  const resolved = path.resolve(dir);
  return resolved !== home && !resolved.startsWith(home + path.sep);
}

function insideApplicationTree(dir) {
  let current = path.resolve(dir);
  for (let i = 0; i < 12; i += 1) {
    const sourceMarker = path.join(current, "source", "collector", "streamdonations", "youtube.py");
    const portableMarker = path.join(current, "collector", "streamdonations", "youtube.py");
    if (fs.existsSync(sourceMarker) || fs.existsSync(portableMarker)) {
      return true;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return false;
    }
    current = parent;
  }
  return false;
}

function dataDir() {
  const requested = process.env.STREAM_DONATIONS_DATA;
  if (requested && !outsideHome(requested) && !insideApplicationTree(requested)) {
    return path.resolve(requested);
  }
  return defaultDataDir();
}

function pythonCommand() {
  if (process.env.STREAM_DONATIONS_PYTHON) {
    return process.env.STREAM_DONATIONS_PYTHON;
  }
  const bundled = path.resolve(__dirname, "..", "python", "bin", "python3");
  if (fs.existsSync(bundled)) {
    return bundled;
  }
  return "python3";
}

function rememberStderr(chunk) {
  stderrTail = (stderrTail + chunk.toString()).slice(-4000);
}

function waitForReady(child) {
  return new Promise((resolve, reject) => {
    let buffer = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) {
        return;
      }
      settled = true;
      reject(new Error("The collector did not start within 20 seconds.\n" + stderrTail));
    }, 20000);

    child.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        const match = /^STREAM_DONATIONS_READY port=(\d+)$/.exec(line);
        if (match && !settled) {
          settled = true;
          clearTimeout(timer);
          resolve(Number(match[1]));
        }
        newline = buffer.indexOf("\n");
      }
    });

    child.on("exit", (code) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      reject(new Error(`Collector exited (${code}).\n${stderrTail}`));
    });
  });
}

function startCollector() {
  const root = collectorRoot();
  fs.mkdirSync(dataDir(), { recursive: true });
  const child = spawn(
    pythonCommand(),
    ["-m", "streamdonations", "serve", "--port", String(PORT)],
    {
      env: {
        ...process.env,
        PYTHONPATH: root,
        PYTHONUNBUFFERED: "1",
        PYTHONNOUSERSITE: "1",
        STREAM_DONATIONS_TOKEN: TOKEN,
        STREAM_DONATIONS_DATA: dataDir(),
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  child.stderr.on("data", rememberStderr);
  child.on("exit", (code) => {
    if (!quitting && code !== 0) {
      dialog.showErrorBox(
        "SuperBerry",
        `The collector stopped (${code}).\n${stderrTail || "No error output."}`,
      );
      app.quit();
    }
  });
  collector = child;
  return child;
}

function windowIcon() {
  const iconPath = path.join(__dirname, "icon.png");
  if (!fs.existsSync(iconPath)) {
    return undefined;
  }
  const image = nativeImage.createFromPath(iconPath);
  return image.isEmpty() ? undefined : image;
}

function stopCollector() {
  if (!collector || collector.exitCode !== null) {
    return;
  }
  collector.kill("SIGTERM");
  const child = collector;
  setTimeout(() => {
    if (child.exitCode === null) {
      child.kill("SIGKILL");
    }
  }, 2000).unref();
}

async function openWindow(port) {
  const origin = `http://127.0.0.1:${port}`;
  await session.defaultSession.cookies.set({
    url: origin + "/",
    name: "sd_session",
    value: TOKEN,
    httpOnly: true,
    sameSite: "strict",
    path: "/",
  });

  const window = new BrowserWindow({
    width: 1220,
    height: 800,
    minWidth: 880,
    minHeight: 640,
    backgroundColor: "#14120f",
    autoHideMenuBar: true,
    title: "SuperBerry",
    icon: windowIcon(),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: true,
    },
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (url.startsWith(origin + "/")) {
      return;
    }
    event.preventDefault();
    shell.openExternal(url);
  });

  mainWindow = window;
  attachChatWebviews(window.webContents);
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = null;
    closeRumbleWindows();
    closeYoutubeWindows();
    closeKickWindows();
  });
  const icon = windowIcon();
  if (icon) {
    window.setIcon(icon);
  }
  await window.loadURL(origin + "/");
  if (icon) {
    window.setIcon(icon);
  }
}

app.setPath("userData", path.join(dataDir(), "window"));

app.whenReady().then(async () => {
  try {
    const child = startCollector();
    const port = await waitForReady(child);
    await openWindow(port);
  } catch (error) {
    dialog.showErrorBox("SuperBerry", String(error.message || error));
    quitting = true;
    stopCollector();
    app.quit();
  }
});

app.on("before-quit", () => {
  quitting = true;
  stopCollector();
});

app.on("window-all-closed", () => {
  app.quit();
});
