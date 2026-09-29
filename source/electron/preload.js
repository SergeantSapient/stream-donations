"use strict";

const { contextBridge, shell } = require("electron");

contextBridge.exposeInMainWorld("desktop", {
  openExternal(url) {
    if (typeof url === "string" && /^https:\/\//.test(url)) {
      return shell.openExternal(url);
    }
    return Promise.resolve();
  },
});
