/* Viktor-style sweep: an owner's Monday morning, driven live via CDP.
 * Scenario 1: background research job (Bento competitors)
 * Scenario 2: document creation (system design overview)
 * Scenario 3: email approval flow (approve → real send to owner)
 * Scenario 4: scheduled automation (weekday inbox summary)
 * Reports PASS/FAIL per stage with DOM + DB evidence.
 */
(async () => {
  const targets = await (await fetch('http://localhost:9223/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const send = (method, params) => new Promise((res) => {
    const mid = ++id;
    const onMsg = (ev) => { const m = JSON.parse(ev.data); if (m.id === mid) { ws.removeEventListener('message', onMsg); res(m); } };
    ws.addEventListener('message', onMsg);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  await new Promise(r => ws.addEventListener('open', r));
  const evalJs = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    if (r.result && r.result.exceptionDetails) return 'EVAL-ERR: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 200);
    return r.result && r.result.result ? r.result.result.value : undefined;
  };
  const status = () => evalJs(`document.getElementById('status').textContent`);
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const say = (text) => evalJs(`(function(){var i=document.getElementById('input'); i.value=${JSON.stringify('')}+${JSON.stringify(arguments[0] || '')}; return 1;})()`);

  async function prompt(text) {
    await evalJs(`(function(){var i=document.getElementById('input'); i.value=${JSON.stringify(text)}; document.getElementById('send').click(); return 1;})()`);
  }
  async function waitOnline(maxSec) {
    for (let i = 0; i < maxSec; i++) {
      await sleep(2000);
      const st = await status();
      if (st === 'ONLINE') return true;
    }
    return false;
  }
  const lastMsg = () => evalJs(`(function(){var m=[...document.querySelectorAll('#transcript .msg')]; return m.length ? m[m.length-1].textContent : '';})()`);
  const results = [];
  const check = (name, ok, evidence) => {
    results.push(`${ok ? 'PASS' : 'FAIL'} ${name} — ${String(evidence).slice(0, 160).replace(/\n/g, ' ')}`);
  };

  // Fresh task for the sweep
  await evalJs(`(function(){document.getElementById('new-task').click(); var n=document.getElementById('task-name'); n.value='Viktor sweep'; document.getElementById('task-create').click(); return 1;})()`);
  await sleep(2500);

  // --- Scenario 1: background research job -------------------------------
  await prompt('Simon, this is a big task, not a quick question: research the productivity app Bento and three competitors, compare their positioning, and write it up as a proper report.');
  let ok = await waitOnline(120);
  const s1reply = await lastMsg();
  // job queued → panel shows it
  await sleep(2000);
  const jobsHtml = await evalJs(`document.getElementById('jobs-list').textContent`);
  check('S1 reply received', ok, s1reply);
  check('S1 background job queued', /#\\d+|job/i.test(jobsHtml) && !/No jobs yet/.test(jobsHtml), jobsHtml.slice(0, 120));

  // wait for the job to finish (JobRunner delivers to Telegram; panel updates)
  let jobDone = false;
  for (let i = 0; i < 90; i++) {
    await sleep(4000);
    const j = await evalJs(`document.getElementById('jobs-list').textContent`);
    if (/DONE|FAILED/i.test(j)) { jobDone = true; break; }
  }
  const jobsFinal = await evalJs(`document.getElementById('jobs-list').textContent`);
  check('S1 job completed', jobDone && /DONE/.test(jobsFinal), jobsFinal.slice(0, 140));

  // --- Scenario 2: document creation -------------------------------------
  await prompt('Draft a one-page system design overview for our Bento competitor — components, data flow, and our moat. Save it as a document.');
  ok = await waitOnline(180);
  const artifacts = await evalJs(`document.getElementById('artifacts-list').textContent`);
  check('S2 document artifact created', ok && !/Nothing created yet/.test(artifacts), artifacts.slice(0, 140));

  // --- Scenario 3: email approval flow ------------------------------------
  await prompt('Email that system design document to jarasf@mindpodtech.com with subject "Viktor sweep — system design"');
  ok = await waitOnline(120);
  const askReply = await lastMsg();
  check('S3 approval asked (not sent blind)', ok && /approve/i.test(askReply), askReply);
  await prompt('approve');
  ok = await waitOnline(120);
  const sentReply = await lastMsg();
  check('S3 approved → sent + confirmed', ok && /sent|done|carried out/i.test(sentReply), sentReply);

  // --- Scenario 4: scheduled automation ------------------------------------
  await prompt('Set up a recurring automation: every weekday at 8:45am, summarize the overnight emails in your inbox.');
  ok = await waitOnline(120);
  const scheds = await evalJs(`document.getElementById('schedules-list').textContent`);
  check('S4 automation scheduled + visible', ok && /8:45|08:45|weekday/i.test(scheds), scheds.slice(0, 140));

  console.log('=== VIKTOR SWEEP RESULTS ===');
  results.forEach(r => console.log(r));
  ws.close(); process.exit(0);
})().catch(e => { console.error('SWEEP ERROR', e.message); process.exit(1); });
