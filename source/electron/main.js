"use strict";

const { app, BrowserWindow, dialog, session, shell } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const TOKEN = crypto.randomBytes(32).toString("hex");
const PORT = process.env.STREAM_DONATIONS_PORT || "47321";

let collector = null;
let quitting = false;
let stderrTail = "";

function collectorRoot() {
  if (process.env.STREAM_DONATIONS_COLLECTOR) {
    return process.env.STREAM_DONATIONS_COLLECTOR;
  }
  return path.resolve(__dirname, "..", "collector");
}

function dataDir() {
  if (process.env.STREAM_DONATIONS_DATA) {
    return process.env.STREAM_DONATIONS_DATA;
  }
  return path.resolve(__dirname, "..", "..", "data");
}

function pythonCommand() {
  if (process.env.STREAM_DONATIONS_PYTHON) {
    return process.env.STREAM_DONATIONS_PYTHON;
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
        "Stream Donations",
        `The collector stopped (${code}).\n${stderrTail || "No error output."}`,
      );
      app.quit();
    }
  });
  collector = child;
  return child;
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
    title: "Stream Donations",
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

  await window.loadURL(origin + "/");
}

app.whenReady().then(async () => {
  try {
    const child = startCollector();
    const port = await waitForReady(child);
    await openWindow(port);
  } catch (error) {
    dialog.showErrorBox("Stream Donations", String(error.message || error));
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
