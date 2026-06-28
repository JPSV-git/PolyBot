/* PolyBot — Frontend Logic */

let ws = null;
let priceChart = null;
let btEquityChart = null;
let paperEquityChart = null;
let paperPollInterval = null;

// ── Tab switching ───────────────────────────────────────────────────────────

document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');

        if (btn.dataset.tab === 'backtest') loadStrategies();
        if (btn.dataset.tab === 'paper') { loadPaperState(); startPaperPolling(); }
        else stopPaperPolling();
    });
});

// ── WebSocket ───────────────────────────────────────────────────────────────

function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.onopen = () => {
        document.getElementById('connStatus').textContent = 'connected';
        document.getElementById('connStatus').style.color = '#22c55e';
    };

    ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);
        if (msg.btc_price) {
            document.getElementById('btcPrice').textContent = '$' + msg.btc_price.toLocaleString(undefined, {maximumFractionDigits: 0});
        }
        if (msg.type === 'price_update' && msg.markets) {
            updateMarketsTable(msg.markets);
        }
    };

    ws.onclose = () => {
        document.getElementById('connStatus').textContent = 'disconnected';
        document.getElementById('connStatus').style.color = '#ef4444';
        setTimeout(connectWS, 3000);
    };

    ws.onerror = () => ws.close();
}

function updateMarketsTable(markets) {
    const tbody = document.querySelector('#marketsTable tbody');
    tbody.innerHTML = markets.map(m =>
        `<tr>
            <td>$${m.target_price.toLocaleString()}</td>
            <td>${m.market_type}</td>
            <td>${m.title.substring(0, 45)}</td>
            <td>${m.yes_bid ? '$' + m.yes_bid.toFixed(3) : '-'}</td>
            <td>${m.yes_ask ? '$' + m.yes_ask.toFixed(3) : '-'}</td>
            <td>${m.yes_mid ? '$' + m.yes_mid.toFixed(3) : '-'}</td>
        </tr>`
    ).join('');
}

// ── Charts tab ──────────────────────────────────────────────────────────────

async function loadStrikes() {
    const resp = await fetch('/api/strikes');
    const data = await resp.json();
    const sel = document.getElementById('chartStrike');
    sel.innerHTML = data.map(s =>
        `<option value="${s.target_price}">${s.markets.map(m => m.market_type === 'dip' ? 'Dip' : 'Reach').join('/')} $${s.target_price.toLocaleString()}</option>`
    ).join('');
    // Pre-select first 3
    for (let i = 0; i < Math.min(3, sel.options.length); i++) sel.options[i].selected = true;
}

async function loadChart() {
    const range = document.getElementById('chartRange').value;
    const sel = document.getElementById('chartStrike');
    const selectedStrikes = Array.from(sel.selectedOptions).map(o => parseFloat(o.value));

    // Get BTC data
    const btcResp = await fetch(`/api/btc-candles?range=${range}`);
    const btcData = await btcResp.json();

    // Get market data for selected strikes
    const marketsResp = await fetch('/api/markets');
    const markets = await marketsResp.json();

    const colors = ['#4f8ff7', '#22c55e', '#ef4444', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4', '#84cc16'];
    const datasets = [];

    // BTC line (right axis)
    if (btcData.length) {
        datasets.push({
            label: 'BTC Price',
            data: btcData.map(c => ({ x: c.ts, y: c.close })),
            borderColor: '#666',
            borderWidth: 1.5,
            pointRadius: 0,
            yAxisID: 'yBTC',
            order: 10,
        });
    }

    // Polymarket lines (left axis)
    let colorIdx = 0;
    for (const strike of selectedStrikes) {
        const strikeMarkets = markets.filter(m => m.target_price === strike);
        for (const m of strikeMarkets) {
            const histResp = await fetch(`/api/market-history?market_id=${m.market_id}&range=${range}`);
            const hist = await histResp.json();
            if (!hist.length) continue;

            const type = m.market_type || (m.title.toLowerCase().includes('dip') ? 'dip' : 'reach');
            datasets.push({
                label: `${type === 'dip' ? 'Dip' : 'Reach'} $${strike.toLocaleString()}`,
                data: hist.map(h => ({ x: h.ts * 1000, y: h.price })),
                borderColor: colors[colorIdx % colors.length],
                borderWidth: 2,
                pointRadius: 0,
                yAxisID: 'yYES',
            });
            colorIdx++;
        }
    }

    if (priceChart) priceChart.destroy();

    const ctx = document.getElementById('priceChart').getContext('2d');
    priceChart = new Chart(ctx, {
        type: 'line',
        data: { datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                tooltip: {
                    callbacks: {
                        title: (items) => {
                            if (!items.length) return '';
                            const ts = items[0].raw.x;
                            return new Date(ts).toLocaleString();
                        },
                        label: (item) => {
                            const v = item.raw.y;
                            if (item.dataset.yAxisID === 'yBTC')
                                return `${item.dataset.label}: $${v.toLocaleString(undefined, {maximumFractionDigits: 0})}`;
                            return `${item.dataset.label}: $${v.toFixed(3)}`;
                        }
                    }
                },
                legend: { labels: { color: '#8b90a0', font: { size: 11 } } },
            },
            scales: {
                x: {
                    type: 'linear',
                    ticks: {
                        color: '#8b90a0',
                        callback: (v) => {
                            const d = new Date(v);
                            return d.toLocaleDateString(undefined, {month:'short', day:'numeric'});
                        },
                        maxTicksLimit: 10,
                    },
                    grid: { color: '#2d3140' },
                },
                yYES: {
                    position: 'left',
                    title: { display: true, text: 'YES Price ($)', color: '#8b90a0' },
                    ticks: { color: '#8b90a0', callback: v => '$' + v.toFixed(2) },
                    grid: { color: '#2d3140' },
                    min: 0, max: 1,
                },
                yBTC: {
                    position: 'right',
                    title: { display: true, text: 'BTC ($)', color: '#666' },
                    ticks: { color: '#666', callback: v => '$' + (v/1000).toFixed(0) + 'k' },
                    grid: { display: false },
                },
            },
        },
    });
}

// ── Backtest tab ────────────────────────────────────────────────────────────

async function loadStrategies() {
    const resp = await fetch('/api/strategies');
    const strats = await resp.json();
    const grid = document.getElementById('strategyGrid');
    grid.innerHTML = Object.entries(strats).map(([id, s]) =>
        `<div class="strategy-card" data-action="${s.action}">
            <h3>[${id}] ${s.name}</h3>
            <div class="desc">${s.description}</div>
            <div class="stats">
                <span>Action: <b>${s.action}</b></span>
                <span>Hold: <b>${s.hold_hours}h</b></span>
                <span>Expected WR: <b>${s.expected_wr}%</b></span>
            </div>
        </div>`
    ).join('');

    // Load months
    const mResp = await fetch('/api/backtest/data-status');
    const mData = await mResp.json();
    const sel = document.getElementById('btMonth');
    sel.innerHTML = Object.entries(mData.months || {}).map(([m, s]) =>
        `<option value="${m}" ${s.ready ? '' : 'disabled'}>${m} (${s.price_points} pts${s.ready ? '' : ' - no data'})</option>`
    ).join('');
}

async function runBacktest() {
    const month = document.getElementById('btMonth').value;
    if (!month) { alert('Select a month'); return; }

    document.getElementById('btStatus').textContent = 'Running backtest...';
    document.getElementById('btResults').style.display = 'none';

    const resp = await fetch('/api/backtest/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            month,
            initial_balance: parseFloat(document.getElementById('btBalance').value) || 1000,
            risk_pct: (parseFloat(document.getElementById('btRisk').value) || 3) / 100,
            max_positions: parseInt(document.getElementById('btMaxPos').value) || 5,
        })
    });
    const data = await resp.json();

    if (data.error) {
        document.getElementById('btStatus').textContent = 'Error: ' + data.error;
        return;
    }

    document.getElementById('btStatus').textContent = '';
    document.getElementById('btResults').style.display = 'block';

    // Metrics
    document.getElementById('btMetrics').innerHTML = [
        ['Final Balance', '$' + data.final_balance.toFixed(2)],
        ['ROI', (data.roi_pct >= 0 ? '+' : '') + data.roi_pct.toFixed(1) + '%', data.roi_pct >= 0],
        ['Total P/L', '$' + data.total_pnl.toFixed(2), data.total_pnl >= 0],
        ['Trades', data.total_trades],
        ['Win Rate', data.wr + '%'],
        ['Sharpe', data.sharpe.toFixed(3)],
        ['Max DD', data.max_drawdown_pct.toFixed(1) + '%'],
        ['W/L/F', `${data.wins}/${data.losses}/${data.flat}`],
    ].map(([label, value, pos]) =>
        `<div class="metric"><div class="label">${label}</div><div class="value ${pos === true ? 'positive' : pos === false ? 'negative' : ''}">${value}</div></div>`
    ).join('');

    // Per-strategy table
    const stBody = document.querySelector('#btStratTable tbody');
    stBody.innerHTML = Object.entries(data.per_strategy || {}).map(([id, s]) =>
        `<tr>
            <td><b>${id}</b></td><td>${s.name}</td><td>${s.trades}</td>
            <td>${s.wins}</td><td>${s.losses}</td><td>${s.wr}%</td>
            <td class="${s.pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}">$${s.pnl.toFixed(2)}</td>
        </tr>`
    ).join('');

    // Trade log
    const trBody = document.querySelector('#btTradesTable tbody');
    trBody.innerHTML = (data.trades || []).map(t =>
        `<tr>
            <td><b>${t.strategy}</b></td><td>${t.action}</td>
            <td>${t.market_title ? t.market_title.substring(0, 35) : ''}</td>
            <td>$${t.entry_price.toFixed(3)}</td><td>$${t.exit_price.toFixed(3)}</td>
            <td class="${t.pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}">$${t.pnl.toFixed(2)}</td>
            <td class="${t.pnl_pct >= 0 ? 'pnl-pos' : 'pnl-neg'}">${t.pnl_pct.toFixed(1)}%</td>
        </tr>`
    ).join('');

    // Equity chart
    if (btEquityChart) btEquityChart.destroy();
    const eq = data.equity_curve || [];
    const ctx = document.getElementById('btEquityChart').getContext('2d');
    btEquityChart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: 'Equity',
                data: eq.map(e => ({ x: e.ts * 1000, y: e.equity })),
                borderColor: '#4f8ff7',
                borderWidth: 2,
                pointRadius: 0,
                fill: { target: 'origin', above: 'rgba(79,143,247,0.1)' },
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    type: 'linear',
                    ticks: { color: '#8b90a0', callback: v => new Date(v).toLocaleDateString(undefined, {month:'short', day:'numeric'}), maxTicksLimit: 8 },
                    grid: { color: '#2d3140' },
                },
                y: {
                    ticks: { color: '#8b90a0', callback: v => '$' + v.toFixed(0) },
                    grid: { color: '#2d3140' },
                },
            }
        }
    });
}

// ── Paper Trading tab ───────────────────────────────────────────────────────

async function loadPaperState() {
    const [stateResp, tradesResp, equityResp] = await Promise.all([
        fetch('/api/paper/state'),
        fetch('/api/paper/trades'),
        fetch('/api/paper/equity'),
    ]);
    const state = await stateResp.json();
    const trades = await tradesResp.json();
    const equity = await equityResp.json();

    // Toggle button
    const btn = document.getElementById('paperToggle');
    btn.textContent = state.running ? 'Stop' : 'Start';
    btn.className = state.running ? 'danger' : 'success';

    // Metrics
    const setMetric = (id, val, cls) => {
        const el = document.getElementById(id);
        el.textContent = val;
        el.className = 'value ' + (cls || '');
    };
    setMetric('pmBalance', '$' + state.balance.toLocaleString(undefined, {minimumFractionDigits: 2}));
    setMetric('pmEquity', '$' + state.equity.toLocaleString(undefined, {minimumFractionDigits: 2}));
    setMetric('pmROI', (state.roi_pct >= 0 ? '+' : '') + state.roi_pct.toFixed(1) + '%', state.roi_pct >= 0 ? 'positive' : 'negative');
    setMetric('pmPnL', '$' + state.total_pnl.toFixed(2), state.total_pnl >= 0 ? 'positive' : 'negative');
    setMetric('pmWR', state.total_trades > 0 ? state.wr + '%' : '-');
    setMetric('pmTrades', state.total_trades);
    setMetric('pmOpen', state.open_positions);

    // Open positions
    const openTrades = trades.filter(t => t.status === 'open');
    const closedTrades = trades.filter(t => t.status === 'closed');

    document.querySelector('#openTradesTable tbody').innerHTML = openTrades.map(t => {
        const created = new Date(t.created_at + 'Z');
        const hoursHeld = ((Date.now() - created.getTime()) / 3600000).toFixed(1);
        return `<tr>
            <td>${t.id}</td><td><b>${t.strategy}</b></td><td>${t.action}</td>
            <td>${t.market_title ? t.market_title.substring(0, 30) : ''}</td>
            <td>$${t.entry_price.toFixed(3)}</td><td>$${t.amount.toFixed(2)}</td>
            <td>${hoursHeld}h / ${t.hold_hours_target}h</td>
        </tr>`;
    }).join('') || '<tr><td colspan="7" style="color:var(--text2)">No open positions</td></tr>';

    document.querySelector('#closedTradesTable tbody').innerHTML = closedTrades.slice(0, 50).map(t =>
        `<tr>
            <td>${t.id}</td><td><b>${t.strategy}</b></td><td>${t.action}</td>
            <td>${t.market_title ? t.market_title.substring(0, 25) : ''}</td>
            <td>$${t.entry_price.toFixed(3)}</td>
            <td>$${(t.exit_price || 0).toFixed(3)}</td>
            <td class="${(t.pnl || 0) >= 0 ? 'pnl-pos' : 'pnl-neg'}">$${(t.pnl || 0).toFixed(2)}</td>
            <td class="${(t.pnl_pct || 0) >= 0 ? 'pnl-pos' : 'pnl-neg'}">${(t.pnl_pct || 0).toFixed(1)}%</td>
            <td>${t.closed_at ? new Date(t.closed_at + 'Z').toLocaleString() : '-'}</td>
        </tr>`
    ).join('') || '<tr><td colspan="9" style="color:var(--text2)">No closed trades</td></tr>';

    // Per-strategy
    document.querySelector('#paperStratTable tbody').innerHTML = Object.entries(state.per_strategy || {}).map(([id, s]) =>
        `<tr>
            <td><b>${id}</b></td><td>${s.name}</td><td>${s.trades}</td>
            <td>${s.wins}</td><td>${s.losses}</td><td>${s.wr}%</td>
            <td class="${s.pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}">$${s.pnl.toFixed(2)}</td>
        </tr>`
    ).join('');

    // Equity chart
    if (paperEquityChart) paperEquityChart.destroy();
    if (equity.length > 1) {
        const ctx = document.getElementById('paperEquityChart').getContext('2d');
        paperEquityChart = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: [{
                    label: 'Equity',
                    data: equity.map(e => ({ x: new Date(e.timestamp + 'Z').getTime(), y: e.equity })),
                    borderColor: '#22c55e',
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: { target: 'origin', above: 'rgba(34,197,94,0.1)' },
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: {
                        type: 'linear',
                        ticks: { color: '#8b90a0', callback: v => new Date(v).toLocaleString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}), maxTicksLimit: 6 },
                        grid: { color: '#2d3140' },
                    },
                    y: {
                        ticks: { color: '#8b90a0', callback: v => '$' + v.toFixed(0) },
                        grid: { color: '#2d3140' },
                    },
                }
            }
        });
    }
}

async function togglePaper() {
    const resp = await fetch('/api/paper/state');
    const state = await resp.json();
    if (state.running) {
        await fetch('/api/paper/stop', { method: 'POST' });
    } else {
        const balance = parseFloat(document.getElementById('paperBalance').value) || 1000;
        await fetch(`/api/paper/start?balance=${balance}`, { method: 'POST' });
    }
    loadPaperState();
}

async function resetPaper() {
    if (!confirm('Reset paper trading? This clears all trades and balance.')) return;
    const balance = parseFloat(document.getElementById('paperBalance').value) || 1000;
    await fetch(`/api/paper/reset?balance=${balance}`, { method: 'POST' });
    loadPaperState();
}

async function updatePaperConfig() {
    await fetch('/api/paper/config', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            risk_pct: (parseFloat(document.getElementById('paperRisk').value) || 3) / 100,
            max_positions: parseInt(document.getElementById('paperMaxPos').value) || 5,
        })
    });
}

function startPaperPolling() {
    stopPaperPolling();
    paperPollInterval = setInterval(loadPaperState, 10000);
}

function stopPaperPolling() {
    if (paperPollInterval) { clearInterval(paperPollInterval); paperPollInterval = null; }
}

// ── Init ────────────────────────────────────────────────────────────────────

connectWS();
loadStrikes().then(() => loadChart());
