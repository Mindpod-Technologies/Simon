// Preload: the shell exposes nothing to the page by default.
// Simon's web UI is served locally and needs no Node access; keep the
// surface minimal. This file exists so contextIsolation has an explicit,
// auditable bridge if a future build needs one (e.g. version display).
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('simonDesktop', {
  platform: process.platform,
  shell: true,
});
