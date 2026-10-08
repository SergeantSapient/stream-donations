"use strict";

const { clipboard, contextBridge, ipcRenderer, shell } = require("electron");

const PLAYER_ACTIONS = ["play", "pause", "mute", "unmute", "live"];

contextBridge.exposeInMainWorld("streamDonationsPlayer", {
  command(action) {
    if (!PLAYER_ACTIONS.includes(action)) return Promise.resolve("ignored");
    return ipcRenderer.invoke("stream-donations-player", action);
  },
});

contextBridge.exposeInMainWorld("desktop", {
  openExternal(url) {
    if (typeof url === "string" && /^https:\/\//.test(url)) {
      return shell.openExternal(url);
    }
    return Promise.resolve();
  },
  readClipboard() {
    return clipboard.readText();
  },
});
