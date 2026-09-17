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

const { app, BrowserWindow, Tray, Menu, nativeImage, shell, ipcMain, dialog, Notification, globalShortcut } = require('electron');
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

// Tiny main-process log: when the window goes missing in the field, this is
// the difference between guessing and knowing.
const LOG_FILE = path.join(os.homedir(), 'simon', 'data', 'logs', 'desktop.log');
function dlog(msg) {
  try {
    fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
    fs.appendFileSync(LOG_FILE, `${new Date().toISOString()} ${msg}\n`);
  } catch (_) { /* logging must never break the app */ }
}
process.on('uncaughtException', (e) => dlog(`uncaughtException: ${e.stack || e}`));
process.on('unhandledRejection', (e) => dlog(`unhandledRejection: ${e && e.stack || e}`));

function healthy(url, timeoutMs = 2000) {
  return new Promise((resolve) => {
    // Any HTTP response — including 303 (auth gate sends / to /setup or
    // /login) and 401 — proves the server is alive. Only connection
    // failure or a 5xx means "down".
    const req = http.get(url, (res) => {
      res.resume();
      resolve(res.statusCode < 500);
    });
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
  tray.on('click', () => { if (mainWindow) mainWindow.show(); });
  renderTrayMenu({ signedOut: true });
  startStatusPolling();
}

// --- Live menu-bar presence ------------------------------------------------
// Polls the backend with the app's own session cookie and keeps the tray
// honest: running jobs, anything awaiting approval, desktop notifications
// when something finishes or needs the owner. This is what makes the
// desktop edition feel alive instead of being a browser tab in a window.

const STATUS_URL = 'http://localhost:8788';
let lastJobStates = {};   // id -> status, to detect transitions
let seenApprovals = new Set();
let latestStatus = { signedOut: true };

async function sessionCookie() {
  try {
    const cookies = await require('electron').session.defaultSession
      .cookies.get({ url: STATUS_URL });
    return cookies.map((c) => `${c.name}=${c.value}`).join('; ');
  } catch (_) { return ''; }
}

function getJSON(pathname, cookie) {
  return new Promise((resolve) => {
    const req = http.get(STATUS_URL + pathname, { headers: { Cookie: cookie } }, (res) => {
      if (res.statusCode !== 200) { res.resume(); return resolve(null); }
      let body = '';
      res.on('data', (d) => { body += d; });
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch (_) { resolve(null); } });
    });
    req.setTimeout(4000, () => { req.destroy(); resolve(null); });
    req.on('error', () => resolve(null));
  });
}

async function pollStatus() {
  if (!(await healthy(SIMON_URL))) {
    latestStatus = { down: true };
    renderTrayMenu(latestStatus);
    return;
  }
  const cookie = await sessionCookie();
  const [jobs, approvals] = await Promise.all([
    getJSON('/api/jobs', cookie),
    getJSON('/api/approvals', cookie),
  ]);
  if (jobs === null && approvals === null) {
    latestStatus = { signedOut: true };
    renderTrayMenu(latestStatus);
    return;
  }
  const running = (jobs && jobs.jobs ? jobs.jobs : []).filter((j) => j.status === 'running');
  const recent = (jobs && jobs.jobs ? jobs.jobs : []);
  const pending = (approvals && approvals.pending) || [];

  // Notify on job transitions to done/failed (only ones we've seen running).
  recent.forEach((j) => {
    const prev = lastJobStates[j.id];
    if (prev === 'running' && (j.status === 'done' || j.status === 'failed')) {
      notifyDesktop(
        j.status === 'done' ? `Job #${j.id} complete` : `Job #${j.id} failed`,
        (j.description || '').slice(0, 100));
    }
    lastJobStates[j.id] = j.status;
  });
  // Notify on NEW pending approvals.
  pending.forEach((p) => {
    if (!seenApprovals.has(p.id)) {
      seenApprovals.add(p.id);
      if (seenApprovals.size > 0 && lastJobStates.__primed) {
        notifyDesktop('Simon needs your approval', p.summary.slice(0, 120));
      }
    }
  });
  // First poll primes state without a notification storm.
  lastJobStates.__primed = true;

  latestStatus = { running, pending };
  renderTrayMenu(latestStatus);
}

function notifyDesktop(title, body) {
  try {
    const n = new Notification({ title: `Simon — ${title}`, body });
    n.on('click', () => { if (mainWindow) mainWindow.show(); });
    n.show();
  } catch (_) { /* notifications best-effort */ }
}

function renderTrayMenu(status) {
  if (!tray) return;
  const items = [];
  let title = '';
  if (status.down) {
    items.push({ label: 'Simon is offline — click to retry', click: () => pollStatus() });
    title = ' ○';
  } else if (status.signedOut) {
    items.push({ label: 'Open Simon Work to sign in for live status', enabled: false });
  } else {
    const running = status.running || [];
    const pending = status.pending || [];
    if (running.length) {
      title = ' ●';
      running.slice(0, 3).forEach((j) => items.push({
        label: `▶ #${j.id} ${String(j.description || '').slice(0, 40)}`,
        enabled: false,
      }));
    } else {
      items.push({ label: 'No jobs running', enabled: false });
    }
    if (pending.length) {
      title = ' ❗';
      items.push({ type: 'separator' });
      pending.slice(0, 3).forEach((p) => items.push({
        label: `❗ ${String(p.summary || '').slice(0, 50)}`,
        click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } },
      }));
    }
  }
  tray.setTitle(title);
  items.push(
    { type: 'separator' },
    { label: 'Quick Ask   ⌥Space', click: () => toggleQuickAsk() },
    { label: 'Open Simon', click: () => { if (mainWindow) { mainWindow.show(); } else { createWindow(); } } },
    { label: 'Monitoring portal', click: () => shell.openExternal('http://localhost:8789/') },
    { type: 'separator' },
    { label: 'Quit Simon Work', click: () => { quitting = true; app.quit(); } },
  );
  tray.setContextMenu(Menu.buildFromTemplate(items));
}

let statusTimer = null;
function startStatusPolling() {
  if (statusTimer) return;
  statusTimer = setInterval(pollStatus, 30000);
  setTimeout(pollStatus, 5000);  // first poll shortly after launch
}

// --- Quick Ask popup --------------------------------------------------------

let quickAsk = null;
function toggleQuickAsk() {
  if (quickAsk && !quickAsk.isDestroyed()) {
    if (quickAsk.isVisible()) { quickAsk.hide(); return; }
    showQuickAsk();
    return;
  }
  quickAsk = new BrowserWindow({
    width: 420, height: 380,
    frame: false, resizable: false, alwaysOnTop: true, skipTaskbar: true,
    show: false,
    backgroundColor: '#0b1116',
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  // Hide on blur — but only AFTER it has genuinely been focused once.
  // Invoked from the global shortcut the app is usually backgrounded, and
  // an immediate blur would hide the popup the instant it appears.
  let focusedOnce = false;
  quickAsk.on('focus', () => { focusedOnce = true; });
  quickAsk.on('blur', () => {
    if (focusedOnce && quickAsk && !quickAsk.isDestroyed()) quickAsk.hide();
  });
  quickAsk.loadURL(STATUS_URL + '/quickask');
  quickAsk.once('ready-to-show', showQuickAsk);
  quickAsk.on('closed', () => { quickAsk = null; });
}

function showQuickAsk() {
  if (!quickAsk || quickAsk.isDestroyed()) return;
  // Steal focus so the field is actually usable when invoked from anywhere.
  try { app.focus({ steal: true }); } catch (_) { /* older Electron */ }
  quickAsk.show();
  quickAsk.focus();
}

app.whenReady().then(() => {
  dlog('whenReady: creating window');
  createWindow().then(() => dlog('createWindow resolved'))
    .catch((e) => dlog(`createWindow FAILED: ${e && e.stack || e}`));
  createTray();
  dlog('tray created');
  globalShortcut.register('Alt+Space', () => toggleQuickAsk());
  app.on('activate', () => { if (mainWindow) mainWindow.show(); });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (statusTimer) clearInterval(statusTimer);
});

app.on('before-quit', () => {
  quitting = true;
  if (backend) { try { backend.kill(); } catch (_) {} }
});

app.on('window-all-closed', () => { /* tray keeps the app alive */ });
