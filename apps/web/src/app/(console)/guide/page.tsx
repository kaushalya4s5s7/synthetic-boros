import Link from "next/link";
import { PageHead } from "@/components/ui";

const SCREENS: [string, string, string, string][] = [
  ["Overview", "/overview", "Is everything connected, and what is the market paying?",
    "Funding now and its 24h average, the best fixed rate on offer, your position status, and the assets the bot scans (gold first, then BTC) with how much their funding moves."],
  ["Offers", "/offers", "Should I enter, and what exactly would I put on?",
    "Type how much to use, press Update offers. The best offer is written as one sentence, then broken into the three orders with the cash each one moves. The table lists every candidate and why it was rejected."],
  ["Position", "/position", "How is my trade doing, and is it safe?",
    "Your locked rate vs funding now, money earned so far, the funding chart with your fixed line and the exit line, each leg’s margin and liquidation price, and the bot’s reasons. Pause and Close all are here."],
  ["Activity", "/activity", "What did the bot do, and why?",
    "Every decision, alert, margin top-up and exit with a timestamp and reason, plus past positions with their final P&L."],
  ["Settings", "/settings", "How do I control the bot?",
    "Start or pause, auto-enter, which assets to scan (untick BTC for RWA only), the risk limits, and Rehearsal: replace the funding number to watch the bot hold and exit."],
];

const STATES: [string, string, string][] = [
  ["No position", "Nothing open.", "Scans every tick; enters only if an offer passes every check."],
  ["Earning", "Funding is above your locked rate.", "Holds."],
  ["Below fixed", "Funding fell under your locked rate, but is still positive.", "Holds: leaving costs more than the small loss."],
  ["Flipped", "Funding just turned negative.", "Holds and counts down: most flips end within 8 hours."],
  ["Exiting", "Funding negative for a whole day, below −25%/yr, or loss past −0.5%.", "Closes all three orders."],
  ["Hedge broken", "One order filled but another didn’t.", "Closes the extra order within seconds."],
  ["Expiring", "Options settle within a day.", "Closes the perp at settlement."],
];

const TERMS: [string, string][] = [
  ["Perpetual (perp)", "A future with no expiry date. To keep its price near the real asset, one side pays the other every 8 hours."],
  ["Funding rate", "That 8-hourly payment, shown per year here. Positive means shorts get paid. It is the floating rate we earn."],
  ["Call / put option", "A call is the right to buy at a set price (the strike) on a set date; a put is the right to sell. Delta’s options settle in cash at 12:00 UTC."],
  ["Synthetic future", "Buying a call and selling a put at the same strike behaves exactly like agreeing today to buy at strike + call − put on the expiry date."],
  ["Fixed rate", "How much more that locked price is than today’s perp price, per year. It is what you pay for the fixed leg."],
  ["Hedged", "Short perp + synthetic future: if the price goes up one side gains what the other loses. Only funding minus the fixed rate is left."],
  ["Contract", "The smallest order size: 0.001 BTC (≈ $80) or 0.001 XAUT (≈ $4)."],
  ["Notional", "The total value the position covers: contracts × 0.001 × price."],
  ["Margin", "Money Delta locks while the position is open. It stays in your account and comes back when you close."],
  ["Liquidation price", "Where Delta would force-close a leg. The bot uses 5x leverage and tops up margin long before that."],
  ["Reserve", "10% of your amount kept free to top up margin if the price moves hard."],
  ["Fee guard", "The bot refuses any trade where fees + spreads would eat more than half of the expected funding gain."],
  ["Spread", "The gap between the best buy and sell price. Crossing it is a cost; the bot measures it on the real order book."],
  ["RWA", "Real-world asset: tokens tracking gold, stocks, oil. On Delta their funding has been flat for 90 days."],
  ["Rehearsal", "Replaces only the funding number the bot reads (testnet funding never moves), so you can watch it hold and exit. Orders are still real testnet orders."],
];

export default function Guide() {
  return (
    <div className="stack">
      <PageHead title="Guide" support="What this platform does, what every screen shows, and every word on it, in plain language." />

      <div className="panel">
        <div className="kicker" style={{ marginBottom: 10 }}>The idea in 30 seconds</div>
        <p className="statement" style={{ fontSize: 26, marginTop: 0 }}>
          Delta pays a floating rate to people who are short its perpetual futures. Options let you lock a fixed rate.
          <span className="faint"> When the fixed rate is cheaper than the floating one, the bot takes both sides and keeps the difference.</span>
        </p>
        <p className="support">
          Like borrowing at a fixed 7% while lending at a floating 11%: you don’t care where the asset’s price goes, only
          about the gap between the two rates. The bot checks every expiry and strike, prices them on the real order book with
          all fees, and only trades when the gap clearly beats the costs.
        </p>
      </div>

      <div>
        <h2 className="section-title">The screens, in the order you use them</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Screen</th><th>Answers</th><th>What you see and do</th></tr></thead>
            <tbody>
              {SCREENS.map(([name, href, q, what]) => (
                <tr key={name}>
                  <td><Link href={href}>{name}</Link></td>
                  <td>{q}</td>
                  <td className="muted">{what}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h2 className="section-title">Position status, and what the bot does</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Status</th><th>Means</th><th>Bot does</th></tr></thead>
            <tbody>
              {STATES.map(([s, m, d]) => (
                <tr key={s}><td><b style={{ fontWeight: 500 }}>{s}</b></td><td>{m}</td><td className="muted">{d}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <h2 className="section-title" style={{ marginTop: 0 }}>Every 30 seconds the bot</h2>
        <ol className="numbered">
          <li><b>01</b>Reads prices, order books and funding (funding from the real market, orders on testnet).</li>
          <li><b>02</b>If a position is open: checks the three orders still match, applies the exit rules, tops up margin if needed.</li>
          <li><b>03</b>If nothing is open: prices every candidate, runs the fee guard, and enters the best one if auto-enter is on.</li>
          <li><b>04</b>Tells Delta it is alive. If it stops, Delta cancels its open orders by itself.</li>
          <li><b>05</b>Writes what it did and why to Activity, but only when something changes.</li>
        </ol>
        <p className="caption">Where the money is: always in your Delta account. The API key lets the bot trade it; the amount on Offers caps how much it may use.</p>
      </div>

      <div>
        <h2 className="section-title">Words on the screens</h2>
        <dl style={{ margin: 0 }}>
          {TERMS.map(([t, d]) => (
            <div key={t} className="guide-term"><dt>{t}</dt><dd>{d}</dd></div>
          ))}
        </dl>
      </div>
    </div>
  );
}
