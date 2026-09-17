import { useState } from 'react'

const TEAL = '#4fd1c5'

// Live product links.
const REPO_URL = 'https://github.com/Mindpod-Technologies/Simon'
const RELEASE_URL = `${REPO_URL}/releases/tag/v1.6.0`
const DOWNLOAD_URL = `${REPO_URL}/releases/download/v1.6.0/Simon.Work-1.6.0-arm64.dmg`
const ISSUES_URL = `${REPO_URL}/issues`
const SALES_EMAIL = 'sales@mindpodtech.com'

// Stripe Payment Links (created in the Stripe dashboard, no code needed).
// Paste the live URLs here to switch Buy Pro from email-us to real checkout.
// After payment, the billing webhook mints + emails the license key and the
// success page displays it. Empty = fall back to the mailto flow.
const STRIPE_PAYMENT_LINK_PRO = ''
const STRIPE_PAYMENT_LINK_BUSINESS = ''
const BUY_PRO_HREF = STRIPE_PAYMENT_LINK_PRO ||
  `mailto:${SALES_EMAIL}?subject=Simon%20Work%20Pro&body=I%27d%20like%20a%20Pro%20license%20key.`
const BUY_BUSINESS_HREF = STRIPE_PAYMENT_LINK_BUSINESS ||
  `mailto:${SALES_EMAIL}?subject=Simon%20Work%20Business`

// Waitlist endpoint: create a free form at formspree.io (or any form
// backend) and paste its URL here, e.g. https://formspree.io/f/abcdwxyz
// Empty = the form falls back to opening the visitor's email client.
const WAITLIST_ENDPOINT = ''
const WAITLIST_EMAIL = 'waitlist@mindpodtech.com'

const FEATURES = [
  {
    title: 'Everywhere you already talk',
    body: 'Web chat, Slack, Telegram, Teams, and a voice CLI — with one shared memory across all of them. Start a thought in the browser, finish it from your phone.',
    icon: '◈',
  },
  {
    title: 'Remembers your business',
    body: 'Persistent long-term memory for facts, people, and commitments — stored in a local SQLite file you own, on hardware you own.',
    icon: '◉',
  },
  {
    title: 'Skills, not prompts',
    body: 'Drop a SKILL.md file into a folder and Simon learns the workflow — briefings, reviews, research reports, decision memos. Nine ship in the box.',
    icon: '▣',
  },
  {
    title: 'Takes real actions',
    body: 'Schedules recurring automations, runs background jobs, fetches the web, reviews documents, and calls any MCP tool server — with an audit log of every action.',
    icon: '⚡',
  },
  {
    title: 'Two-brain efficiency',
    body: 'A fast model answers simple turns in seconds; a larger smart model handles complex work, tools, and judgment. Routed automatically, benchmarked openly.',
    icon: '◬',
  },
  {
    title: 'Observable by default',
    body: 'A live monitoring portal shows every turn, model route, latency, and eval score. An AI employee you can actually supervise.',
    icon: '◍',
  },
]

const TIERS = [
  {
    name: 'Trial',
    price: 'Free',
    cadence: 'forever',
    cta: 'Download',
    href: DOWNLOAD_URL,
    featured: false,
    items: ['Full source, personal use', 'All 9 skills + all interfaces', 'Community support via issues', 'No license key required'],
  },
  {
    name: 'Pro',
    price: '$12',
    cadence: '/month per seat',
    alt: 'or $149 lifetime',
    cta: 'Buy Pro',
    href: BUY_PRO_HREF,
    featured: true,
    items: ['Commercial use, one operator', '12 months of updates', 'Email support', 'Private release downloads'],
  },
  {
    name: 'Business',
    price: '$49',
    cadence: '/month per seat',
    cta: 'Talk to us',
    href: BUY_BUSINESS_HREF,
    featured: false,
    items: ['Seat volume discounts', 'White-label rights', 'Priority support + onboarding call', 'Roadmap influence, invoice/PO flow'],
  },
]

const FAQ = [
  {
    q: 'Where does my data go?',
    a: 'Nowhere. Memory, documents, and conversation history live in local files on your machine. The only outbound calls are to the LLM endpoint you choose — and with local models, there are none.',
  },
  {
    q: 'Do I need a GPU or a special machine?',
    a: 'Simon Work is tuned for Apple Silicon Macs with 24 GB of memory (a Mac Mini works beautifully). Smaller machines can use the cloud-LLM option instead of local models.',
  },
  {
    q: 'What does "no per-token fees" mean?',
    a: 'You pay for the software once (or monthly for updates and support). Your AI usage itself is free — the models run on your hardware. Bring a cloud API key only if you want one.',
  },
  {
    q: 'What if you disappear?',
    a: 'You keep running. Source-available code, offline license keys, no activation server, no kill switch. Your deployment is yours.',
  },
  {
    q: 'Can I white-label it for my clients?',
    a: 'Yes — Business tier includes white-label rights: rename the persona, rebrand the web UI, deploy it as your own assistant.',
  },
  {
    q: 'How do updates work?',
    a: 'One command — run.py update fetches the newest release tag, runs the full test suite as a smoke gate, restarts services, and rolls back automatically if anything fails.',
  },
]

function Nav() {
  return (
    <nav className="fixed top-0 inset-x-0 z-50 border-b border-[#1e2a38] bg-[#0a0e14]/85 backdrop-blur">
      <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-md border border-[#4fd1c5]/60 flex items-center justify-center text-[#4fd1c5] font-mono text-sm">S</div>
          <span className="font-mono tracking-[0.3em] text-sm text-[#c9d6e3]">SIMON&nbsp;WORK</span>
        </div>
        <div className="hidden md:flex items-center gap-7 text-sm text-[#5c7186]">
          <a href="#features" className="hover:text-[#c9d6e3] transition-colors">Features</a>
          <a href="#how" className="hover:text-[#c9d6e3] transition-colors">How it works</a>
          <a href="#pricing" className="hover:text-[#c9d6e3] transition-colors">Pricing</a>
          <a href="#faq" className="hover:text-[#c9d6e3] transition-colors">FAQ</a>
        </div>
        <a href={REPO_URL} className="text-sm font-medium px-4 py-1.5 rounded-md bg-[#4fd1c5] text-[#0a0e14] hover:bg-[#63e0d0] transition-colors">Get Simon</a>
      </div>
    </nav>
  )
}

function Hero() {
  return (
    <header className="relative pt-36 pb-24 px-6 overflow-hidden">
      <div className="absolute inset-0 opacity-[0.13]" style={{
        backgroundImage: 'linear-gradient(#1e2a38 1px, transparent 1px), linear-gradient(90deg, #1e2a38 1px, transparent 1px)',
        backgroundSize: '44px 44px',
      }} />
      <div className="absolute left-1/2 -top-24 -translate-x-1/2 w-[640px] h-[420px] rounded-full opacity-20 blur-3xl" style={{ background: TEAL }} />
      <div className="relative max-w-4xl mx-auto text-center">
        <p className="font-mono text-xs tracking-[0.35em] text-[#4fd1c5] mb-6">SELF-HOSTED · LOCAL MODELS · NO PER-TOKEN FEES</p>
        <h1 className="text-5xl md:text-7xl font-semibold text-[#e8f0f7] leading-[1.05] tracking-tight">
          Your self-hosted<br />AI employee.
        </h1>
        <p className="mt-7 text-lg text-[#7d93a8] max-w-2xl mx-auto leading-relaxed">
          Simon answers on Slack, Telegram, web, and voice — remembers your business,
          takes real actions with tools, and never sends your data to anyone's cloud.
        </p>
        <div className="mt-10 flex items-center justify-center gap-4 flex-wrap">
          <a href={DOWNLOAD_URL} className="px-7 py-3 rounded-lg bg-[#4fd1c5] text-[#0a0e14] font-medium hover:bg-[#63e0d0] transition-colors">Start free</a>
          <a href="#how" className="px-7 py-3 rounded-lg border border-[#1e2a38] text-[#c9d6e3] hover:border-[#4fd1c5]/50 transition-colors">See how it works</a>
        </div>
        <div className="mt-14 max-w-xl mx-auto rounded-lg border border-[#1e2a38] bg-[#0d131c] text-left font-mono text-sm">
          <div className="flex gap-1.5 px-4 py-2.5 border-b border-[#1e2a38]">
            <span className="w-2.5 h-2.5 rounded-full bg-[#1e2a38]" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#1e2a38]" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#1e2a38]" />
          </div>
          <div className="px-4 py-3.5 text-[#7d93a8]">
            <span className="text-[#4fd1c5]">$</span> git clone https://github.com/Mindpod-Technologies/Simon.git && ./Simon/deploy/install_mac.sh<br />
            <span className="text-[#3d5468]"># or just download the app — one click: models, services, web UI</span>
          </div>
        </div>
      </div>
    </header>
  )
}

function PrivacyBanner() {
  return (
    <section className="border-y border-[#1e2a38] bg-[#0d131c]">
      <div className="max-w-6xl mx-auto px-6 py-8 grid md:grid-cols-3 gap-6 text-center">
        {[
          ['No telemetry', 'Nothing phones home. Ever.'],
          ['Offline license keys', 'No activation server, no kill switch.'],
          ['Air-gap friendly', 'Runs fully offline with local models.'],
        ].map(([t, s]) => (
          <div key={t}>
            <div className="font-mono text-sm text-[#4fd1c5] tracking-widest">{t.toUpperCase()}</div>
            <div className="text-[#7d93a8] text-sm mt-1.5">{s}</div>
          </div>
        ))}
      </div>
    </section>
  )
}

function Features() {
  return (
    <section id="features" className="max-w-6xl mx-auto px-6 py-24">
      <p className="font-mono text-xs tracking-[0.35em] text-[#4fd1c5] mb-4">WHAT HE DOES</p>
      <h2 className="text-3xl md:text-4xl font-semibold text-[#e8f0f7] tracking-tight max-w-xl">
        An employee, not a chat window.
      </h2>
      <div className="mt-12 grid md:grid-cols-3 gap-5">
        {FEATURES.map((f) => (
          <div key={f.title} className="rounded-xl border border-[#1e2a38] bg-[#0d131c] p-6 hover:border-[#4fd1c5]/40 transition-colors">
            <div className="text-[#4fd1c5] text-xl mb-4">{f.icon}</div>
            <h3 className="text-[#e8f0f7] font-medium mb-2">{f.title}</h3>
            <p className="text-[#7d93a8] text-sm leading-relaxed">{f.body}</p>
          </div>
        ))}
      </div>
    </section>
  )
}

function HowItWorks() {
  return (
    <section id="how" className="border-y border-[#1e2a38] bg-[#0d131c]">
      <div className="max-w-6xl mx-auto px-6 py-24">
        <p className="font-mono text-xs tracking-[0.35em] text-[#4fd1c5] mb-4">HOW IT WORKS</p>
        <h2 className="text-3xl md:text-4xl font-semibold text-[#e8f0f7] tracking-tight">Your hardware. Your models. Your rules.</h2>
        <div className="mt-12 grid md:grid-cols-4 gap-5">
          {[
            ['01', 'Install', 'One command on a Mac Mini or VPS. Models, services, and the web UI set themselves up.'],
            ['02', 'Connect', 'Add Slack, Telegram, Teams, email, calendar — or nothing at all. Every channel is optional.'],
            ['03', 'Teach', 'Drop SKILL.md files into a folder to teach your workflows. No code, no fine-tuning.'],
            ['04', 'Supervise', 'Watch every turn, tool call, and eval score in the monitoring portal. Update with one command.'],
          ].map(([n, t, s]) => (
            <div key={n} className="relative">
              <div className="font-mono text-[#2a3a4c] text-4xl">{n}</div>
              <h3 className="text-[#e8f0f7] font-medium mt-3 mb-2">{t}</h3>
              <p className="text-[#7d93a8] text-sm leading-relaxed">{s}</p>
            </div>
          ))}
        </div>
        <div className="mt-14 rounded-xl border border-[#1e2a38] bg-[#0a0e14] p-6 font-mono text-xs md:text-sm text-[#7d93a8] overflow-x-auto">
          <span className="text-[#c9d6e3]">Slack · Telegram · Web · Voice</span>
          {'  →  '}
          <span className="text-[#4fd1c5]">Router</span>
          {'  →  '}
          fast model <span className="text-[#3d5468]">(seconds)</span> | smart model <span className="text-[#3d5468]">(judgment)</span>
          {'  →  '}
          tools · memory · skills
        </div>
      </div>
    </section>
  )
}

function Pricing() {
  return (
    <section id="pricing" className="max-w-6xl mx-auto px-6 py-24">
      <p className="font-mono text-xs tracking-[0.35em] text-[#4fd1c5] mb-4">PRICING</p>
      <h2 className="text-3xl md:text-4xl font-semibold text-[#e8f0f7] tracking-tight">Pay for software. Not for tokens.</h2>
      <p className="mt-4 text-[#7d93a8] max-w-xl">Your AI usage is free — models run on your hardware. You pay for the product, updates, and support.</p>
      <div className="mt-12 grid md:grid-cols-3 gap-5">
        {TIERS.map((t) => (
          <div key={t.name} className={`rounded-xl border p-7 flex flex-col ${t.featured ? 'border-[#4fd1c5] bg-[#0d131c] shadow-[0_0_40px_-12px_rgba(79,209,197,0.35)]' : 'border-[#1e2a38] bg-[#0d131c]'}`}>
            <div className="flex items-baseline justify-between">
              <h3 className="text-[#e8f0f7] font-medium">{t.name}</h3>
              {t.featured && <span className="font-mono text-[10px] tracking-widest text-[#4fd1c5] border border-[#4fd1c5]/40 rounded px-2 py-0.5">POPULAR</span>}
            </div>
            <div className="mt-5 flex items-baseline gap-1.5">
              <span className="text-4xl font-semibold text-[#e8f0f7]">{t.price}</span>
              <span className="text-[#5c7186] text-sm">{t.cadence}</span>
            </div>
            {t.alt && <div className="text-[#4fd1c5] text-sm font-mono mt-1">{t.alt}</div>}
            <ul className="mt-6 space-y-2.5 text-sm text-[#7d93a8] flex-1">
              {t.items.map((i) => (
                <li key={i} className="flex gap-2.5"><span className="text-[#4fd1c5]">✓</span>{i}</li>
              ))}
            </ul>
            <a href={t.href} className={`mt-7 text-center py-2.5 rounded-lg text-sm font-medium transition-colors ${t.featured ? 'bg-[#4fd1c5] text-[#0a0e14] hover:bg-[#63e0d0]' : 'border border-[#1e2a38] text-[#c9d6e3] hover:border-[#4fd1c5]/50'}`}>{t.cta}</a>
          </div>
        ))}
      </div>
    </section>
  )
}

function Faq() {
  return (
    <section id="faq" className="border-t border-[#1e2a38] bg-[#0d131c]">
      <div className="max-w-3xl mx-auto px-6 py-24">
        <p className="font-mono text-xs tracking-[0.35em] text-[#4fd1c5] mb-4">FAQ</p>
        <h2 className="text-3xl font-semibold text-[#e8f0f7] tracking-tight mb-10">Fair questions.</h2>
        <div className="space-y-6">
          {FAQ.map((f) => (
            <div key={f.q} className="rounded-lg border border-[#1e2a38] bg-[#0a0e14] p-6">
              <h3 className="text-[#e8f0f7] font-medium mb-2">{f.q}</h3>
              <p className="text-[#7d93a8] text-sm leading-relaxed">{f.a}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function Waitlist() {
  const [email, setEmail] = useState('')
  const [state, setState] = useState<'idle' | 'sending' | 'done' | 'error'>('idle')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!email.includes('@')) return
    if (!WAITLIST_ENDPOINT) {
      window.location.href = `mailto:${WAITLIST_EMAIL}?subject=Simon Work waitlist&body=Add me: ${encodeURIComponent(email)}`
      setState('done')
      return
    }
    setState('sending')
    try {
      const res = await fetch(WAITLIST_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ email }),
      })
      setState(res.ok ? 'done' : 'error')
    } catch {
      setState('error')
    }
  }

  return (
    <section className="border-t border-[#1e2a38]">
      <div className="max-w-2xl mx-auto px-6 py-20 text-center">
        <p className="font-mono text-xs tracking-[0.35em] text-[#4fd1c5] mb-4">EARLY ACCESS</p>
        <h2 className="text-3xl font-semibold text-[#e8f0f7] tracking-tight">Hire Simon first.</h2>
        <p className="mt-3 text-[#7d93a8]">Design-partner onboarding opens soon. Leave your email — one message when it's your turn, nothing else.</p>
        {state === 'done' ? (
          <p className="mt-8 font-mono text-sm text-[#4fd1c5]">✓ You're on the list. Simon will be in touch.</p>
        ) : (
          <form onSubmit={submit} className="mt-8 flex gap-3 max-w-md mx-auto">
            <input
              type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              className="flex-1 rounded-lg border border-[#1e2a38] bg-[#0d131c] px-4 py-3 text-sm text-[#e8f0f7] placeholder-[#3d5468] outline-none focus:border-[#4fd1c5]/60"
            />
            <button type="submit" disabled={state === 'sending'}
              className="px-6 py-3 rounded-lg bg-[#4fd1c5] text-[#0a0e14] text-sm font-medium hover:bg-[#63e0d0] transition-colors disabled:opacity-50">
              {state === 'sending' ? '…' : 'Join'}
            </button>
          </form>
        )}
        {state === 'error' && <p className="mt-3 text-sm text-[#f6ad55]">Something hiccuped — try again in a moment.</p>}
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer className="border-t border-[#1e2a38]">
      <div className="max-w-6xl mx-auto px-6 py-10 flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <div className="w-6 h-6 rounded-md border border-[#4fd1c5]/60 flex items-center justify-center text-[#4fd1c5] font-mono text-xs">S</div>
          <span className="font-mono tracking-[0.3em] text-xs text-[#5c7186]">SIMON WORK</span>
        </div>
        <p className="text-[#3d5468] text-xs font-mono">your data never leaves your server</p>
        <div className="flex gap-6 text-xs text-[#5c7186]">
          <a href={REPO_URL} className="hover:text-[#c9d6e3] transition-colors">Docs</a>
          <a href={RELEASE_URL} className="hover:text-[#c9d6e3] transition-colors">Changelog</a>
          <a href={ISSUES_URL} className="hover:text-[#c9d6e3] transition-colors">Support</a>
        </div>
      </div>
    </footer>
  )
}

export default function Home() {
  return (
    <div className="min-h-screen bg-[#0a0e14] text-[#c9d6e3] antialiased">
      <Nav />
      <Hero />
      <PrivacyBanner />
      <Features />
      <HowItWorks />
      <Pricing />
      <Waitlist />
      <Faq />
      <Footer />
    </div>
  )
}
