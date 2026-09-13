// Simon Work desktop shell.
//
// The app is a thin native wrapper around the local Simon backend:
//   1. If http://localhost:8788 answers, load it — done.
//   2. If not but a Simon install exists (~/simon with .venv), spawn
//      `run.py server` as a child process and wait for health.
//   3. If no install exists, show the first-run screen (firstrun.html)
//      which walks the user through the one-command installer.
//
// Tray icon keeps Simon alive in the background; closing the window hides
// it rather than quitting (an employee who stops when you look away is not
// an employee).

const { app, BrowserWindow, Tray, Menu, nativeImage, shell, ipcMain, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');
const os = require('os');

const SIMON_URL = 'http://localhost:8788/';
const SIMON_HOME = process.env.SIMON_HOME || path.join(os.homedir(), 'simon');

let mainWindow = null;
let tray = null;
let backend = null;
let quitting = false;

function healthy(url, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => resolve(res.statusCode === 200));
    req.setTimeout(timeoutMs, () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

function simonInstalled() {
  const py = process.platform === 'win32'
    ? path.join(SIMON_HOME, '.venv', 'Scripts', 'python.exe')
    : path.join(SIMON_HOME, '.venv', 'bin', 'python');
  return fs.existsSync(py) && fs.existsSync(path.join(SIMON_HOME, 'run.py'));
}

function startBackend() {
  if (backend) return;
  const py = process.platform === 'win32'
    ? path.join(SIMON_HOME, '.venv', 'Scripts', 'python.exe')
    : path.join(SIMON_HOME, '.venv', 'bin', 'python');
  backend = spawn(py, ['run.py', 'server'], {
    cwd: SIMON_HOME,
    stdio: 'ignore',
    detached: false,
  });
  backend.on('exit', () => { backend = null; });
}

async function waitForBackend(maxMs = 60000) {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    if (await healthy(SIMON_URL)) return true;
    await new Promise((r) => setTimeout(r, 1500));
  }
  return false;
}

// --- IPC for the first-run screen -----------------------------------------
// The first-run page is local and unprivileged; these two handlers are the
// whole bridge: check whether Ollama is serving, and open the Ollama
// download page in the system browser. No shell execution from the page.

function ollamaUp() {
  return healthy('http://localhost:11434/api/version');
}

app.whenReady().then(() => {
  ipcMain.handle('ollama:status', () => ollamaUp());
  ipcMain.handle('ollama:download', () => shell.openExternal('https://ollama.com/download'));
});

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#0a0e14',
    title: 'Simon Work',
    icon: nativeImage.createFromPath(path.join(__dirname, 'icon.png')),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.setMenuBarVisibility(false);
  mainWindow.on('close', (e) => {
    if (!quitting) { e.preventDefault(); mainWindow.hide(); }
  });

  if (await healthy(SIMON_URL)) {
    mainWindow.loadURL(SIMON_URL);
    return;
  }

  if (simonInstalled()) {
    mainWindow.loadFile('firstrun.html', { query: { state: 'starting' } });
    startBackend();
    if (await waitForBackend()) {
      mainWindow.loadURL(SIMON_URL);
    } else {
      mainWindow.loadFile('firstrun.html', { query: { state: 'failed' } });
    }
  } else {
    mainWindow.loadFile('firstrun.html', { query: { state: 'missing' } });
  }
}

function createTray() {
  const img = nativeImage.createFromPath(path.join(__dirname, 'icon.png')).resize({ width: 18, height: 18 });
  tray = new Tray(img);
  tray.setToolTip('Simon Work');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open Simon', click: () => { if (mainWindow) { mainWindow.show(); } else { createWindow(); } } },
    { label: 'Monitoring portal', click: () => shell.openExternal('http://localhost:8789/') },
    { type: 'separator' },
    { label: 'Quit Simon Work', click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on('click', () => { if (mainWindow) mainWindow.show(); });
}

app.whenReady().then(() => {
  createWindow();
  createTray();
  app.on('activate', () => { if (mainWindow) mainWindow.show(); });
});

app.on('before-quit', () => {
  quitting = true;
  if (backend) { try { backend.kill(); } catch (_) {} }
});

app.on('window-all-closed', () => { /* tray keeps the app alive */ });
