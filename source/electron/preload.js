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
  rumbleSignIn() {
    return ipcRenderer.invoke("stream-donations-rumble", { action: "login" });
  },
  rumbleChat(url) {
    return ipcRenderer.invoke("stream-donations-rumble", { action: "chat", url });
  },
  rumbleSend(text, url) {
    if (typeof text !== "string" || typeof url !== "string") return Promise.resolve("refused");
    return ipcRenderer.invoke("stream-donations-rumble", { action: "send", text, url });
  },
  youtubeSignIn() {
    return ipcRenderer.invoke("stream-donations-youtube", { action: "login" });
  },
  youtubeChat(url) {
    return ipcRenderer.invoke("stream-donations-youtube", { action: "chat", url });
  },
  kickSignIn() {
    return ipcRenderer.invoke("stream-donations-kick", { action: "login" });
  },
  kickChat(url) {
    return ipcRenderer.invoke("stream-donations-kick", { action: "chat", url });
  },
});
