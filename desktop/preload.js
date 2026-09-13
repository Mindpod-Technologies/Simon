// Preload: the shell exposes a tiny, auditable bridge to the page.
// Simon's web UI is served locally and needs no Node access; the first-run
// screen needs exactly two things: is Ollama up, and open its download page.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('simonDesktop', {
  platform: process.platform,
  shell: true,
  ollamaStatus: () => ipcRenderer.invoke('ollama:status'),
  ollamaDownload: () => ipcRenderer.invoke('ollama:download'),
});
