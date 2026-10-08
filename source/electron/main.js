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
