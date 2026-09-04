// Smart Trader Terminal — Web App Client JavaScript

let currentSymbol = "FX:EURUSD";
let currentInterval = "60";
let tvWidget = null;

// Инициализация Telegram WebApp SDK
document.addEventListener("DOMContentLoaded", () => {
    if (window.Telegram && window.Telegram.WebApp) {
        const tg = window.Telegram.WebApp;
        tg.ready();
        tg.expand();
        tg.setHeaderColor('#131722');
        tg.setBackgroundColor('#131722');
    }

    // Инициализация графика TradingView
    initTradingView(currentSymbol, currentInterval);

    // Первичная загрузка данных
    loadSignals();
    loadStats();
    loadEvents();
});

// ── Переключение вкладок ──
function switchTab(tabId, btn) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

    const targetTab = document.getElementById(`tab-${tabId}`);
    if (targetTab) targetTab.classList.add('active');
    if (btn) btn.classList.add('active');

    if (tabId === 'signals') loadSignals();
    if (tabId === 'stats') loadStats();
    if (tabId === 'calendar') loadEvents();
}

// ── Интерактивный виджет TradingView ──
function initTradingView(symbol, interval) {
    const container = document.getElementById('tradingview_widget');
    if (!container) return;
    container.innerHTML = '';

    if (typeof TradingView !== 'undefined') {
        tvWidget = new TradingView.widget({
            "autosize": true,
            "symbol": symbol,
            "interval": interval,
            "timezone": "Asia/Tashkent",
            "theme": "dark",
            "style": "1",
            "locale": "ru",
            "toolbar_bg": "#131722",
            "enable_publishing": false,
            "hide_top_toolbar": false,
            "hide_legend": false,
            "save_image": false,
            "container_id": "tradingview_widget",
            "studies": [
                "MASimple@tv-basicstudies",
                "RSI@tv-basicstudies"
            ],
            "overrides": {
                "paneProperties.background": "#131722",
                "paneProperties.vertGridProperties.color": "#1e222d",
                "paneProperties.horzGridProperties.color": "#1e222d",
                "mainSeriesProperties.candleStyle.upColor": "#089981",
                "mainSeriesProperties.candleStyle.downColor": "#f23645",
                "mainSeriesProperties.candleStyle.wickUpColor": "#089981",
                "mainSeriesProperties.candleStyle.wickDownColor": "#f23645"
            }
        });
    }
}

function changePair(newPair) {
    currentSymbol = newPair;
    initTradingView(currentSymbol, currentInterval);
}

function changeTF(tf) {
    currentInterval = tf;
    document.querySelectorAll('.tf-btn').forEach(btn => btn.classList.remove('active'));
    event.target.classList.add('active');
    initTradingView(currentSymbol, currentInterval);
}

// ── Загрузка сигналов ──
async function loadSignals() {
    const container = document.getElementById('signals-container');
    if (!container) return;
    container.innerHTML = '<div class="loader">Синхронизация с радаром...</div>';

    try {
        const res = await fetch('/api/signals');
        const data = await res.json();

        const activeList = data.active || [];
        const pendingList = data.pending || [];
        const allSignals = [...activeList, ...pendingList];

        if (allSignals.length === 0) {
            container.innerHTML = `
                <div class="loader" style="padding: 40px 10px;">
                    ⚪ <b>В данный момент нет активных позиций.</b><br>
                    <span style="font-size: 11px; color: #787b86;">Сканер 17 инструментов непрерывно отслеживает рынок.</span>
                </div>
            `;
            return;
        }

        let html = '';
        allSignals.forEach(sig => {
            const isLong = sig.direction === 'LONG';
            const dirClass = isLong ? 'long' : 'short';
            const dirLabel = isLong ? '🟢 BUY' : '🔴 SELL';
            const orderType = (sig.order_type || '').replace('_', ' ');
            const tag = sig.tag_emoji || '🔥';

            const entry = sig.entry_price ? Number(sig.entry_price).toFixed(sig.symbol.includes('JPY') ? 3 : (sig.symbol.includes('XAU') ? 2 : 5)) : '--';
            const sl = sig.stop_loss ? Number(sig.stop_loss).toFixed(sig.symbol.includes('JPY') ? 3 : (sig.symbol.includes('XAU') ? 2 : 5)) : '--';
            const tp1 = sig.take_profit_1 ? Number(sig.take_profit_1).toFixed(sig.symbol.includes('JPY') ? 3 : (sig.symbol.includes('XAU') ? 2 : 5)) : '--';
            const rr = sig.risk_reward ? `1:${sig.risk_reward}` : '1:2.5';
            const statusLabel = sig.status === 'ACTIVE' ? '⚡ В РЫНКЕ' : '⏳ ОЖИДАНИЕ';

            html += `
                <div class="signal-card">
                    <div class="card-top">
                        <div class="card-symbol">${sig.symbol} <span style="font-size: 14px;">[${tag}]</span></div>
                        <div class="card-dir ${dirClass}">${dirLabel} ${orderType}</div>
                    </div>
                    <div class="card-grid">
                        <div class="card-col">
                            <span class="card-lbl">ВХОД</span>
                            <span class="card-val">${entry}</span>
                        </div>
                        <div class="card-col">
                            <span class="card-lbl">СТОП (SL)</span>
                            <span class="card-val sl">${sl}</span>
                        </div>
                        <div class="card-col">
                            <span class="card-lbl">ТЕЙК (TP1)</span>
                            <span class="card-val tp">${tp1}</span>
                        </div>
                    </div>
                    <div class="card-status-bar">
                        <span>R:R = <b>${rr}</b></span>
                        <span style="color: ${sig.status === 'ACTIVE' ? '#089981' : '#f0b90b'}; font-weight: 700;">${statusLabel}</span>
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="loader" style="color: #f23645;">Ошибка загрузки сигналов: ${e.message}</div>`;
    }
}

// ── Загрузка статистики ──
async function loadStats() {
    try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        const s = data.stats || {};

        document.getElementById('stat-winrate').innerText = `${s.win_rate || 0}%`;
        const pnl = s.total_pnl_pips || 0;
        const pnlEl = document.getElementById('stat-pnl');
        pnlEl.innerText = `${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)} pips`;
        pnlEl.style.color = pnl >= 0 ? '#089981' : '#f23645';

        document.getElementById('stat-total').innerText = s.total_closed || 0;
        document.getElementById('stat-rr').innerText = s.avg_rr ? `1:${s.avg_rr}` : '1:2.5';
    } catch (e) {
        console.error("loadStats error", e);
    }
}

// ── Загрузка календаря новостей ──
async function loadEvents() {
    const container = document.getElementById('calendar-container');
    if (!container) return;
    container.innerHTML = '<div class="loader">Загрузка макро-событий...</div>';

    try {
        const res = await fetch('/api/events');
        const data = await res.json();
        const events = data.events || [];

        if (events.length === 0) {
            container.innerHTML = '<div class="loader">Нет важных релизов на ближайшие 48ч.</div>';
            return;
        }

        let html = '';
        events.forEach(ev => {
            html += `
                <div class="event-card">
                    <div class="event-title">${ev.title}</div>
                    <div class="event-meta">
                        <span>🏳️ <b>${ev.country}</b> | 🕐 ${ev.date_str} ${ev.time_str}</span>
                        <span style="color: #f23645; font-weight: 800;">🔴 HIGH IMPACT</span>
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="loader" style="color: #f23645;">Ошибка календаря: ${e.message}</div>`;
    }
}
