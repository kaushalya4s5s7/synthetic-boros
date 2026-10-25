import Link from "next/link";
import "./landing.css";
import { LiveStrip } from "@/components/LiveStrip";

const STEPS = [
  { n: "01", t: "Scan", d: "Every 30 seconds the bot prices every BTC option expiry 21–45 days out and every strike, by walking the live Delta order book for your exact size." },
  { n: "02", t: "Guard", d: "It enters only if fees and spreads stay under half of the expected funding gain and the net edge clears 0.15%. Every rejected strike shows why." },
  { n: "03", t: "Enter", d: "Leverage is forced to 5x, then: sell put, buy call, short perp. Each leg priced from a fresh book. If one leg fails, the others are unwound." },
  { n: "04", t: "Watch", d: "Funding arrives every 8 hours. The bot tops up margin, holds through short flips, and exits when funding stays negative for a full day." },
];

const PROTECTIONS = [
  "Fee guard: no trade if costs exceed 50% of the expected funding edge.",
  "Depth-walk pricing: never trusts a ticker quote that can move within a second.",
  "Leg-failure unwind: one filled leg alone is closed within seconds.",
  "Leverage forced to 5x. Delta defaults to 200x on mainnet.",
  "Margin top-up below 50% health, deleverage if the reserve runs out, still hedged.",
  "Exit on a full negative day, an emergency at −25%/yr, or a −0.5% hard stop.",
  "No early exit in the last 3 days, when exiting costs more than holding.",
  "Delta heartbeat cancels open orders if the bot goes silent.",
];

const FAQ = [
  ["Is this a bet on BTC going up or down?", "No. Short one perp plus one synthetic future (long call, short put at the same strike) has zero net BTC exposure. The P&L is the funding you receive minus the fixed rate you locked, minus fees."],
  ["Where does the fixed rate come from?", "Put-call parity. A call minus a put at the same strike and expiry behaves exactly like a dated future priced at strike + call − put. Comparing that price with the perp gives the fixed rate the options market is charging."],
  ["Why read funding from mainnet when trading on testnet?", "Testnet funding is pinned at 10.95%/yr, so it can never flip. Decisions use the real market's funding so the bot behaves the way it would in production. Orders still go to testnet."],
  ["What happens when funding turns negative?", "On Delta most negative stretches end within 8 hours, so the bot holds and shows a countdown. If the 24-hour average drops below zero it closes all three legs. In two years of data this kept the average profit and cut the worst loss by 60%."],
  ["What does it cost?", "Delta's maker/taker fees read from the API plus 18% GST, and the spread on each option. The console shows them in dollars before you enter, and the bot refuses trades where they would eat the edge."],
];

export default function Landing() {
  return (
    <div className="lp-bg">
      <div className="lp-sheet">
        <header className="lp-header">
          <Link href="/" className="lp-logo">Synthetic Boros</Link>
          <nav className="lp-nav" aria-label="Sections">
            <a href="#how">How it works</a>
            <a href="#guard">Fee guard</a>
            <a href="#risk">Risk</a>
            <a href="#evidence">Evidence</a>
            <a href="#faq">Questions</a>
          </nav>
          <div className="lp-actions">
            <a className="btn secondary lp-hide-sm" href="#how">How it works</a>
            <Link className="btn primary" href="/overview">Open console</Link>
          </div>
        </header>

        <section className="lp-hero">
          <span className="lp-eyebrow">Built on Delta Exchange</span>
          <h1>Lock a fixed rate. <span className="lp-faint">Earn Delta’s floating funding.</span></h1>
          <p className="lp-hero-support">
            Delta pays funding on perpetual futures every 8 hours. Options let you lock a fixed rate below it.
            Synthetic Boros captures the difference, hedged against BTC’s price, and refuses any trade where
            fees would eat the gain.
          </p>
          <div className="lp-hero-buttons">
            <Link className="btn primary lg" href="/overview">Open console</Link>
            <a className="btn secondary lg" href="#evidence">See the evidence</a>
          </div>
          <LiveStrip />
        </section>

        <section className="lp-stage-wrap" aria-label="Product preview">
          <div className="lp-stage">
            <div className="lp-window">
              <div className="lp-window-bar">
                <span className="lp-lights"><i /><i /><i /></span>
                <span className="lp-url">localhost:3000/position</span>
                <span className="lp-status">Earning</span>
              </div>
              <div className="lp-window-body">
                <div className="lp-kicker">BTC · 27 Nov 2026 · strike $80,000 · 33 days</div>
                <div className="lp-window-title">You locked <b>7.12%</b>. Funding <b>10.95%</b>. <span className="lp-faint">Spread +3.83%/yr.</span></div>
                <div className="lp-legs">
                  <div><span>Short perp</span><b>−3</b><em>liq $96,297 · +19.7%</em></div>
                  <div><span>Short put</span><b>−3</b><em>margin $22.03</em></div>
                  <div><span>Long call</span><b>+3</b><em>paid in full</em></div>
                </div>
                <div className="lp-caption">Real fill on Delta testnet, 25 Oct 2026 · net BTC exposure 0</div>
              </div>
            </div>
          </div>
        </section>

        <section className="lp-statement">
          <span className="lp-mark" aria-hidden="true"><i /><i /><i /><i /></span>
          <p>
            Delta’s funding sat at 10.95% a year for 60% of the last two years. Options priced a fixed rate near 7%.
            <span className="lp-faint"> The gap is the trade.</span>
          </p>
        </section>

        <section className="lp-section" id="how">
          <div className="lp-split">
            <div>
              <span className="lp-eyebrow">How it works</span>
              <h2>Three legs, one rate swap.</h2>
            </div>
            <p className="lp-support">
              The perp leg receives the floating rate. The call and put together form a dated future with a fixed price.
              Long one, short the other: BTC’s price cancels out and only the two rates remain.
            </p>
          </div>
          <div className="lp-diagram">
            <div className="lp-node">
              <span className="lp-node-label">Floating</span>
              <div className="lp-pill">Short BTCUSD perp</div>
              <div className="lp-node-note">receives funding every 8h</div>
            </div>
            <div className="lp-wire" aria-hidden="true" />
            <div className="lp-hub">Net BTC<br />exposure 0</div>
            <div className="lp-wire" aria-hidden="true" />
            <div className="lp-node">
              <span className="lp-node-label">Fixed</span>
              <div className="lp-pill">Buy call + sell put</div>
              <div className="lp-node-note">F = strike + call − put</div>
            </div>
          </div>
          <div className="lp-formula">
            P&amp;L = funding received − (F − perp entry) − fees &nbsp;·&nbsp; fixed rate = (F ÷ perp − 1) × 365 ÷ days
          </div>
          <div className="lp-steps">
            {STEPS.map((s) => (
              <div key={s.n} className="lp-step">
                <span className="lp-num">{s.n}</span>
                <h3>{s.t}</h3>
                <p>{s.d}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="lp-section lp-wash" id="guard">
          <div className="lp-center">
            <span className="lp-kicker-c">Fee guard</span>
            <h2 className="lp-center-title">The bot’s best trade is often no trade.</h2>
            <p className="lp-support lp-center-support">
              Every candidate is priced against the real order book, with Delta’s fees and GST included. If costs would eat
              more than half the funding gain, it says no, and tells you why.
            </p>
          </div>
          <div className="lp-receipt">
            <div className="lp-receipt-q"><i />Why didn’t the bot trade at 14:20 on 25 Oct?</div>
            <div className="lp-receipt-a">
              Mainnet funding averaged <b>5.02%</b>. The cheapest fixed rate was <b>6.89%</b>, so there was no edge, and option spreads
              cost <b>0.127%</b> of notional against a 0.12% limit. All 10 strikes rejected.
            </div>
            <div className="lp-chips">
              <span className="lp-chip">/v2/l2orderbook</span>
              <span className="lp-chip">FUNDING:BTCUSD candles</span>
              <span className="lp-chip">/v2/products fees</span>
            </div>
          </div>
        </section>

        <section className="lp-section" id="risk">
          <div className="lp-split">
            <div>
              <span className="lp-eyebrow">Risk management</span>
              <h2>Every risk has a rule and an automatic action.</h2>
            </div>
            <p className="lp-support">
              Each threshold comes from two years of Delta funding history, a 4,000-path margin stress test, and live testnet orders.
            </p>
          </div>
          <ol className="lp-list">
            {PROTECTIONS.map((p, i) => (
              <li key={p}><span className="lp-num">{String(i + 1).padStart(2, "0")}</span>{p}</li>
            ))}
          </ol>
        </section>

        <section className="lp-section" id="evidence">
          <div className="lp-split">
            <div>
              <span className="lp-eyebrow">Evidence</span>
              <h2>Tested on history, then on Delta.</h2>
            </div>
            <p className="lp-support">Research scripts live in the repository. Live numbers are from real testnet orders.</p>
          </div>
          <div className="lp-stats">
            <div className="lp-stat"><b>17,502</b><span>hours of Delta mainnet funding analysed</span></div>
            <div className="lp-stat"><b>−60%</b><span>worst loss with the 24h exit rule, same average profit</span></div>
            <div className="lp-stat"><b>0%</b><span>hedge breaks at 5x with a 10% reserve (Monte Carlo)</span></div>
            <div className="lp-stat"><b>7.12%</b><span>fixed rate locked from live testnet fills</span></div>
          </div>
        </section>

        <section className="lp-section" id="faq">
          <div className="lp-split">
            <div>
              <span className="lp-eyebrow">Questions</span>
              <h2>What people ask first.</h2>
            </div>
          </div>
          <div className="lp-faq">
            {FAQ.map(([q, a]) => (
              <details key={q}>
                <summary>{q}<span className="lp-chev" aria-hidden="true" /></summary>
                <p>{a}</p>
              </details>
            ))}
          </div>
        </section>

        <footer className="lp-footer">
          <div className="lp-footer-top">
            <div>
              <h2>Start with the console.</h2>
              <p>Testnet orders, mainnet funding, every decision explained.</p>
              <Link className="btn primary lg" href="/overview">Open console</Link>
            </div>
            <div className="lp-footer-meta">
              <span>Built for</span>
              <b>TokenIQ · Delta Exchange</b>
            </div>
          </div>
          <div className="lp-legal">Testnet only. Not investment advice.</div>
          <div className="lp-wordmark" aria-hidden="true">Boros</div>
        </footer>
      </div>
    </div>
  );
}
