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
const https = require('https');
const os = require('os');

const SIMON_URL = 'http://localhost:8788/';
const SIMON_HOME = process.env.SIMON_HOME || path.join(os.homedir(), 'simon');
// Source for the backend install. For private repos pass a token URL via
// SIMON_REPO_URL (https://x-access-token:<pat>@github.com/org/repo.git);
// once public releases exist this can point at the public repo.
const SIMON_REPO = process.env.SIMON_REPO_URL ||
  'https://github.com/Mindpod-Technologies/simon.git';

// Official Ollama installers per platform.
const OLLAMA_DOWNLOAD = {
  darwin: 'https://ollama.com/download/Ollama-darwin.zip',
  win32: 'https://ollama.com/download/OllamaSetup.exe',
};

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
// The first-run page is local and unprivileged; the bridge below is the
// whole attack surface: fixed commands, no page-supplied arguments, one
// install at a time. Progress is streamed back via 'install:progress'.

function ollamaUp() {
  return healthy('http://localhost:11434/api/version');
}

let installRunning = false;

function progress(step, text) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('install:progress', { step, text });
  }
}

function download(url, dest, step, redirects = 5) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith('https') ? https : http;
    const req = client.get(url, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        if (redirects <= 0) return reject(new Error('too many redirects'));
        return resolve(download(res.headers.location, dest, step, redirects - 1));
      }
      if (res.statusCode !== 200) {
        res.resume();
        return reject(new Error(`download failed: HTTP ${res.statusCode}`));
      }
      const total = Number(res.headers['content-length'] || 0);
      let got = 0, lastPct = -1;
      const out = fs.createWriteStream(dest);
      res.on('data', (chunk) => {
        got += chunk.length;
        if (total) {
          const pct = Math.floor((got / total) * 100);
          if (pct !== lastPct && pct % 5 === 0) {
            lastPct = pct;
            progress(step, `downloading… ${pct}%`);
          }
        }
      });
      res.pipe(out);
      out.on('finish', () => out.close(() => resolve(dest)));
      out.on('error', reject);
    });
    req.setTimeout(30000, () => { req.destroy(new Error('download timed out')); });
    req.on('error', reject);
  });
}

function runStep(step, cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { ...opts });
    child.stdout.on('data', (d) => {
      d.toString().split('\n').filter(Boolean)
        .forEach((line) => progress(step, line.slice(0, 200)));
    });
    child.stderr.on('data', (d) => {
      d.toString().split('\n').filter(Boolean)
        .forEach((line) => progress(step, line.slice(0, 200)));
    });
    child.on('error', reject);
    child.on('exit', (code) =>
      code === 0 ? resolve() : reject(new Error(`${cmd} exited with code ${code}`)));
  });
}

async function installOllama() {
  const url = OLLAMA_DOWNLOAD[process.platform];
  if (!url) throw new Error(`auto-install not supported on ${process.platform} — get it from ollama.com`);
  const dest = path.join(os.tmpdir(), path.basename(url));
  progress('ollama', 'downloading the Ollama installer…');
  await download(url, dest, 'ollama');
  if (process.platform === 'darwin') {
    progress('ollama', 'installing Ollama.app…');
    await runStep('ollama', 'ditto', ['-xk', dest, '/Applications']);
    progress('ollama', 'launching Ollama…');
    await runStep('ollama', 'open', ['-a', 'Ollama']);
  } else {
    progress('ollama', 'running the installer (a minute or two)…');
    await runStep('ollama', dest, ['/VERYSILENT', '/NORESTART']);
  }
  // Wait for the engine to serve.
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (await ollamaUp()) { progress('ollama', '✓ Ollama is installed and running.'); return; }
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error('Ollama installed but not answering yet — reopen this app in a moment.');
}

async function installSimon() {
  if (!fs.existsSync(path.join(SIMON_HOME, '.git'))) {
    progress('simon', 'downloading Simon…');
    await runStep('simon', 'git', ['clone', '--depth', '1', SIMON_REPO, SIMON_HOME]);
  } else {
    progress('simon', 'updating Simon…');
    await runStep('simon', 'git', ['-C', SIMON_HOME, 'pull', '--ff-only']);
  }
  progress('simon', 'installing (Python, models — the big download, once)…');
  if (process.platform === 'win32') {
    await runStep('simon', 'powershell',
      ['-ExecutionPolicy', 'Bypass', '-NonInteractive', '-File',
       path.join(SIMON_HOME, 'deploy', 'install_windows.ps1')],
      { cwd: SIMON_HOME });
  } else {
    await runStep('simon', 'bash',
      [path.join(SIMON_HOME, 'deploy', 'install_mac.sh'), '--noninteractive'],
      { cwd: SIMON_HOME });
  }
  progress('simon', 'starting Simon…');
  startBackend();
  if (await waitForBackend(120000)) {
    progress('simon', '✓ Simon is live — taking you there…');
    return;
  }
  throw new Error('installed, but Simon did not start — check ~/simon/data/logs/');
}

app.whenReady().then(() => {
  ipcMain.handle('ollama:status', () => ollamaUp());
  ipcMain.handle('ollama:download', () => shell.openExternal('https://ollama.com/download'));
  ipcMain.handle('ollama:install', async () => {
    if (installRunning) return { ok: false, error: 'an install is already running' };
    installRunning = true;
    try { await installOllama(); return { ok: true }; }
    catch (e) { return { ok: false, error: String(e.message || e) }; }
    finally { installRunning = false; }
  });
  ipcMain.handle('simon:install', async () => {
    if (installRunning) return { ok: false, error: 'an install is already running' };
    installRunning = true;
    try { await installSimon(); return { ok: true }; }
    catch (e) { return { ok: false, error: String(e.message || e) }; }
    finally { installRunning = false; }
  });
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
