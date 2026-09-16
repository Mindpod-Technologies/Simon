// Preload: the shell exposes a tiny, auditable bridge to the page.
// Simon's web UI is served locally and needs no Node access; the first-run
// screen gets fixed-purpose installers only — no arbitrary command execution.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('simonDesktop', {
  platform: process.platform,
  shell: true,
  ollamaStatus: () => ipcRenderer.invoke('ollama:status'),
  ollamaDownload: () => ipcRenderer.invoke('ollama:download'),
  ollamaInstall: () => ipcRenderer.invoke('ollama:install'),
  simonInstall: () => ipcRenderer.invoke('simon:install'),
  onProgress: (cb) => {
    const listener = (_e, data) => cb(data);
    ipcRenderer.on('install:progress', listener);
    return () => ipcRenderer.removeListener('install:progress', listener);
  },
});
