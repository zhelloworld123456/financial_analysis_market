const $ = id => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value; };
const node = (tag, value, className) => {
  const element = document.createElement(tag);
  element.textContent = value;
  if (className) element.className = className;
  return element;
};
const fmt = (value, digits = 2) => value === null || value === undefined ? '—' : Number(value).toFixed(digits);
const pct = (value, digits = 2) => value === null || value === undefined ? '—' : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(digits)}%`;
const money = value => value === null || value === undefined ? '—' : `${(Number(value) / 1e8).toFixed(2)}亿`;
const directionClass = value => Number(value) > 0.05 ? 'up' : Number(value) < -0.05 ? 'down' : 'flat';
const setList = (id, items) => {
  const list = $(id);
  list.replaceChildren();
  items.forEach(item => list.append(node('li', item)));
};

const viewConfig = {
  timing: { title: '好时机', eyebrow: 'MARKET TIMING', subtitle: '判断趋势、行业强弱与事件驱动的研究窗口', explorer: true, sort: 'good_timing' },
  stock: { title: '好股票', eyebrow: 'BUSINESS QUALITY', subtitle: '从盈利、成长、现金流和稳定性评估企业质量', explorer: true, sort: 'good_stock' },
  price: { title: '好价格', eyebrow: 'VALUATION RESEARCH', subtitle: '结合行业特征比较估值与盈利、净资产参考区间', explorer: true, sort: 'good_price' },
  factors: { title: '七维评估', eyebrow: 'SEVEN-FACTOR SCORECARD', subtitle: '盈利、成长、现金、财务安全、估值、市场风险与治理', explorer: true, sort: 'overall' },
  history: { title: '股票历史分析', eyebrow: 'HISTORICAL ANALYSIS', subtitle: '观察长期收益、回撤、波动和年度表现', explorer: false, sort: 'overall' },
  agents: { title: '多agent股票分析', eyebrow: 'MULTI-AGENT RESEARCH', subtitle: '五个专家并行研究，Kimi 主编综合结论', explorer: false, sort: 'overall' },
  chat: { title: '多 Agent 对话', eyebrow: 'MULTI-AGENT CONVERSATION', subtitle: '连续追问、Agent 公开讨论与全链路实时追踪', explorer: false, sort: 'overall' },
  weekly: { title: '一周走势预测', eyebrow: 'FIVE-TRADING-DAY OUTLOOK', subtitle: '结合热点、消息、行业周期和业绩的五个交易日预测', explorer: false, sort: 'overall' }
};

let currentView = 'timing';
let stockData = null;
let screenData = null;
let filteredItems = [];
let chatSessionId = null;
let chatRunning = false;
let chatAvailable = true;
let activeTraceId = null;
const chatTraces = new Map();
const renderedChatMessages = new Set();

function switchView(view) {
  currentView = view;
  const config = viewConfig[view];
  document.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  text('view-title', config.title);
  text('view-eyebrow', config.eyebrow);
  text('view-subtitle', config.subtitle);
  document.querySelectorAll('.view-panel').forEach(panel => panel.classList.add('hidden'));
  $(`view-${view}`).classList.remove('hidden');
  $('empty-state').classList.toggle('hidden', view === 'chat' || Boolean(stockData));
  $('stock-content').classList.toggle('hidden', view === 'chat' || !stockData);
  $('explorer').classList.toggle('hidden', !config.explorer);
  $('sort-by').value = config.sort;
  if (screenData && config.explorer) applyFilters();
}

document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));

fetch('/api/capabilities').then(response => response.json()).then(data => {
  if (!data.ai.available) {
    $('use-ai').checked = false;
    $('use-ai').disabled = true;
    text('ai-capability', '未配置 KIMI_API_KEY，使用规则模型');
  } else {
    text('ai-capability', `${data.ai.model} · 五专家并行 + 主编`);
  }
  if (!data.conversation.available) {
    chatAvailable = false;
    $('chat-send').disabled = true;
    text('chat-capability', '未配置 KIMI_API_KEY，多 Agent 对话不可用');
  } else {
    text('chat-capability', `${data.conversation.model} · 五角色顺序公开讨论`);
  }
}).catch(() => {
  text('ai-capability', '模型配置检查失败，将自动降级');
  text('chat-capability', '模型配置检查失败');
});

$('search-form').addEventListener('submit', async event => {
  event.preventDefault();
  const useAI = $('use-ai').checked;
  $('submit').disabled = true;
  $('submit').textContent = '研究中…';
  $('loading').textContent = useAI
    ? '正在获取金融数据，并行运行五个专家 Agent，随后由 Kimi 主编汇总，请稍候…'
    : '正在获取行情、财务、板块、公告和历史数据，请稍候…';
  $('loading').classList.remove('hidden');
  $('error').classList.add('hidden');
  try {
    const response = await fetch('/api/analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: $('query').value.trim(), use_ai: useAI })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '分析请求失败');
    stockData = data;
    renderStock(data);
    $('empty-state').classList.add('hidden');
    $('stock-content').classList.remove('hidden');
    text('data-status', `${data.stock.name} · 已更新`);
    switchView(currentView === 'chat' ? 'timing' : currentView);
  } catch (error) {
    $('error').textContent = error.message || '网络连接失败，请稍后重试';
    $('error').classList.remove('hidden');
  } finally {
    $('submit').disabled = false;
    $('submit').textContent = '分析';
    $('loading').classList.add('hidden');
  }
});

function renderStock(data) {
  const { stock, quote, scores } = data;
  text('stock-name', stock.name);
  text('stock-code', `${stock.market} ${stock.code}`);
  text('stock-industry', quote.industry);
  text('quote-time', `行情时间 ${quote.quoted_at.replace('T', ' ')} · ${quote.source}`);
  text('last-price', fmt(quote.price));
  text('price-change', pct(quote.change_pct));
  $('price-change').className = directionClass(quote.change_pct);
  text('header-score-stock', fmt(scores.good_stock.score, 1));
  text('header-score-price', fmt(scores.good_price.score, 1));
  text('header-score-timing', fmt(scores.good_timing.score, 1));
  text('header-score-overall', fmt(scores.overall_score, 1));
  text('sources', `数据来源：${data.sources.join('、')}`);
  text('generated-at', `生成时间：${data.meta.generated_at.replace('T', ' ')}`);
  renderTiming(data);
  renderWeekly(data);
  renderQuality(data);
  renderValuation(data);
  renderFactors(data);
  renderHistory(data);
  renderAI(data.ai_research);
}

function fillScore(prefix, score, scoreId, labelId, reasonsId) {
  text(scoreId, fmt(score.score, 1));
  text(labelId, score.label);
  setList(reasonsId, score.reasons);
}

function renderTiming(data) {
  const { technical, forecast, scores } = data;
  fillScore('timing', scores.good_timing, 'timing-score-big', 'timing-label', 'timing-reasons');
  text('forecast-weekly', `五日 ${pct(forecast.weekly_return_pct)}`);
  text('technical-signal', technical.signal);
  text('ma5', fmt(technical.ma5));
  text('ma20', fmt(technical.ma20));
  text('ma60', fmt(technical.ma60));
  text('rsi', fmt(technical.rsi14));
  text('momentum', pct(technical.momentum_20d_pct));
  text('volatility', `${fmt(technical.annual_volatility_pct)}%`);
  text('support', fmt(technical.support_20d));
  text('resistance', fmt(technical.resistance_20d));
  text('macd', fmt(technical.macd_histogram, 4));
  const body = $('forecast-body');
  body.replaceChildren();
  forecast.days.forEach(day => {
    const row = document.createElement('tr');
    row.append(
      node('td', `${day.date} 周${day.weekday}`),
      node('td', day.direction, `direction ${directionClass(day.change_pct)}`),
      node('td', pct(day.change_pct), directionClass(day.change_pct)),
      node('td', fmt(day.predicted_close)),
      node('td', `${fmt(day.lower_close_80)} ~ ${fmt(day.upper_close_80)}`)
    );
    body.append(row);
  });
  renderAnnouncements(scores.announcement_review.items);
  renderChart('timing-chart', data.history, forecast.days, true);
}

function renderWeekly(data) {
  const forecast = data.forecast;
  text('weekly-method', forecast.method);
  text('weekly-direction', forecast.weekly_direction);
  text('weekly-return', pct(forecast.weekly_return_pct));
  $('weekly-return').className = directionClass(forecast.weekly_return_pct);
  text('weekly-statistical', pct(forecast.statistical_weekly_return_pct));
  text('weekly-factor-impact', pct(forecast.factor_weekly_impact_pct));
  text('weekly-factor-note', forecast.factor_note);
  text('weekly-intraday-note', forecast.intraday_note);
  const dayCards = $('weekly-day-cards');
  dayCards.replaceChildren();
  forecast.days.forEach(day => {
    const card = document.createElement('article');
    card.className = 'day-card';
    const head = document.createElement('div');
    head.className = 'day-card-head';
    head.append(node('strong', `${day.date.slice(5)} 周${day.weekday}`), node('span', day.direction, `direction ${directionClass(day.change_pct)}`));
    const details = document.createElement('dl');
    const values = [
      ['预计开盘', fmt(day.predicted_open)],
      ['低点 / 高点涨跌幅', `${pct(day.low_change_pct)} / ${pct(day.high_change_pct)}`],
      ['预计振幅', `${fmt(day.amplitude_pct)}%`],
      ['80%收盘涨跌幅', `${pct(day.close_lower_change_pct)} ~ ${pct(day.close_upper_change_pct)}`]
    ];
    values.forEach(([label, value]) => details.append(node('dt', label), node('dd', value)));
    card.append(
      head,
      node('span', pct(day.change_pct), `day-change ${directionClass(day.change_pct)}`),
      node('div', `${fmt(day.predicted_low)} ~ ${fmt(day.predicted_high)}`, 'day-range'),
      details
    );
    dayCards.append(card);
  });
  const body = $('weekly-body');
  body.replaceChildren();
  forecast.days.forEach(day => {
    const row = document.createElement('tr');
    row.append(
      node('td', `${day.date} 周${day.weekday}`),
      node('td', day.direction, `direction ${directionClass(day.change_pct)}`),
      node('td', fmt(day.predicted_open)),
      node('td', `${fmt(day.predicted_low)} ~ ${fmt(day.predicted_high)}`),
      node('td', `${pct(day.low_change_pct)} / ${pct(day.high_change_pct)}`),
      node('td', `${fmt(day.amplitude_pct)}%`),
      node('td', pct(day.statistical_change_pct)),
      node('td', pct(day.factor_adjustment_pct), directionClass(day.factor_adjustment_pct)),
      node('td', pct(day.change_pct), directionClass(day.change_pct)),
      node('td', fmt(day.predicted_close)),
      node('td', `${fmt(day.lower_close_80)} ~ ${fmt(day.upper_close_80)}`)
    );
    body.append(row);
  });
  const factorGrid = $('weekly-factor-grid');
  factorGrid.replaceChildren();
  forecast.factors.forEach(factor => {
    const impactClass = factor.impact === '利好' ? 'positive' : factor.impact === '利空' ? 'negative' : '';
    const card = document.createElement('article');
    card.className = `factor-card ${impactClass}`;
    card.append(
      node('span', factor.impact, `impact ${impactClass}`),
      node('strong', factor.name),
      node('span', `${factor.daily_adjustment_pct >= 0 ? '+' : ''}${fmt(factor.daily_adjustment_pct, 4)}% / 日`, 'factor-adjustment'),
      node('p', factor.summary)
    );
    factorGrid.append(card);
  });
  const market = data.market_environment;
  const indexGrid = $('market-index-grid');
  indexGrid.replaceChildren();
  if (market && market.indices.length) {
    market.indices.forEach(index => {
      const item = document.createElement('div');
      item.className = 'market-index-item';
      item.append(node('strong', index.name), node('span', `20日 ${pct(index.momentum_20d_pct)}`), node('span', `60日 ${pct(index.momentum_60d_pct)}`), node('span', index.above_ma20 ? 'MA20上' : 'MA20下'));
      indexGrid.append(item);
    });
  } else {
    indexGrid.append(node('p', '指数环境数据不足，市场因子按中性处理。', 'small-note'));
  }
  const hotspotGrid = $('hotspot-grid');
  hotspotGrid.replaceChildren();
  if (market && market.hotspots.length) {
    market.hotspots.forEach(hotspot => {
      const item = document.createElement('div');
      item.className = `hotspot-item ${hotspot.is_stock_sector ? 'current' : ''}`;
      item.append(node('strong', hotspot.name), node('span', `5日 ${pct(hotspot.change_5d_pct)}`), node('span', `1日 ${pct(hotspot.change_1d_pct)}`), node('span', `10日 ${pct(hotspot.change_10d_pct)}`), node('span', `涨/跌 ${hotspot.up_count}/${hotspot.down_count}`));
      hotspotGrid.append(item);
    });
  } else {
    hotspotGrid.append(node('p', '热点榜数据暂时不可用。', 'small-note'));
  }
  const newsList = $('weekly-news-list');
  newsList.replaceChildren();
  const newsItems = forecast.news_review.items;
  if (!newsItems.length) newsList.append(node('p', '近期未获取到相关新闻，舆情因子按中性处理。', 'small-note'));
  newsItems.forEach(news => {
    const item = document.createElement('div');
    item.className = 'announcement-item';
    const impactClass = news.impact === '利好' ? 'positive' : news.impact === '利空' ? 'negative' : '';
    const link = node('a', news.title);
    link.href = news.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.title = `${news.source} · ${news.is_company_related ? '公司相关' : '市场相关，不计入公司舆情'}${news.summary ? ` · ${news.summary}` : ''}`;
    item.append(node('span', news.date.slice(0, 10), 'announcement-date'), node('span', news.impact, `impact ${impactClass}`), link);
    newsList.append(item);
  });
  setList('weekly-risk-list', [...forecast.risk_notes, forecast.interval_note, forecast.intraday_note, forecast.date_note]);
  renderChart('weekly-chart', data.history, forecast.days, true);
}

function renderQuality(data) {
  const { scores, financials } = data;
  fillScore('stock', scores.good_stock, 'stock-score-big', 'quality-label', 'stock-reasons');
  const latest = financials[0] || {};
  text('annual-roe', pct(scores.good_stock.metrics.annualized_roe_pct));
  text('revenue-growth', pct(latest.revenue_yoy_pct));
  text('profit-growth', pct(latest.net_profit_yoy_pct));
  text('gross-margin', latest.gross_margin_pct === null || latest.gross_margin_pct === undefined ? '—' : `${fmt(latest.gross_margin_pct)}%`);
  text('cash-conversion', fmt(scores.good_stock.metrics.cash_conversion));
  text('fund-summary', data.fundamental.summary);
  renderFinancials('stock-finance-body', financials);
}

function renderValuation(data) {
  const { scores, quote } = data;
  fillScore('price', scores.good_price, 'price-score-big', 'valuation-label', 'price-reasons');
  const metrics = scores.good_price.metrics;
  text('pe', fmt(quote.pe_dynamic));
  text('pb', fmt(quote.pb));
  text('fair-pe', fmt(metrics.fair_pe));
  text('fair-pb', fmt(metrics.fair_pb));
  text('value-range', metrics.value_low === null ? '数据不足' : `${fmt(metrics.value_low)} ~ ${fmt(metrics.value_high)}`);
  text('industry-note', `${scores.industry_style}行业模板：${scores.industry_note}`);
  renderChart('price-history-chart', data.history, [], false);
}

function renderFactors(data) {
  const { scores, financials, historical, quote } = data;
  const seven = scores.seven_factor;
  const latest = financials[0] || {};
  const multiple = value => value === null || value === undefined ? '—' : `${fmt(value)}倍`;
  const netMargin = latest.net_profit !== null && latest.net_profit !== undefined && latest.revenue
    ? latest.net_profit / latest.revenue * 100 : null;
  const cashRatio = latest.operating_cash_flow !== null && latest.operating_cash_flow !== undefined && latest.net_profit > 0
    ? latest.operating_cash_flow / latest.net_profit : null;
  text('factor-summary', scores.summary);
  text('factor-overall-score', fmt(seven.overall_score, 1));
  text('factor-verdict', seven.verdict);
  text('factor-coverage', `${seven.coverage.note}。当前覆盖 ${seven.coverage.available_dimensions}/7 个维度。`);
  text('factor-report-date', latest.report_type ? `${latest.report_type} · ${latest.report_date}` : '暂无财报');
  $('risk-veto-banner').classList.toggle('hidden', !seven.veto.triggered);
  text('risk-veto-text', seven.veto.triggered
    ? `${seven.veto.flags.map(item => item.detail).join('；')}。综合分不能抵消上述风险。`
    : '');
  const grid = $('factor-dimension-grid');
  grid.replaceChildren();
  seven.dimensions.forEach(dimension => {
    const card = document.createElement('article');
    card.className = `factor-dimension-card ${dimension.status === 'insufficient' ? 'insufficient' : ''}`;
    const top = document.createElement('div');
    top.className = 'factor-dimension-top';
    top.append(node('strong', dimension.name), node('span', `权重 ${dimension.weight}%`));
    const score = document.createElement('div');
    score.className = 'factor-dimension-score';
    score.append(node('strong', dimension.score === null ? '—' : fmt(dimension.score, 1)), node('span', dimension.label));
    const track = document.createElement('div');
    track.className = 'factor-score-track';
    const fill = document.createElement('span');
    fill.style.width = `${dimension.score || 0}%`;
    track.append(fill);
    const reasons = document.createElement('ul');
    dimension.reasons.slice(0, 2).forEach(reason => reasons.append(node('li', reason)));
    card.append(top, score, track, reasons);
    grid.append(card);
  });
  const metrics = [
    ['年化 ROE', pct(scores.good_stock.metrics.annualized_roe_pct), '报告期简单年化，非全年预测'],
    ['销售毛利率', latest.gross_margin_pct === null || latest.gross_margin_pct === undefined ? '—' : pct(latest.gross_margin_pct), '同行业、同口径比较'],
    ['归母净利率', pct(netMargin), '归母净利润 ÷ 营收'],
    ['营收 / 净利同比', `${pct(latest.revenue_yoy_pct)} / ${pct(latest.net_profit_yoy_pct)}`, '关注低基数和一次性收益'],
    ['经营现金流', money(latest.operating_cash_flow), '同一报告期累计值'],
    ['自由现金流 FCF', money(latest.free_cash_flow), '经营现金流 − 资本开支'],
    ['经营现金流 / 净利润', multiple(cashRatio), '检验利润现金含量'],
    ['资本开支', money(latest.capital_expenditure), '购建长期资产支付现金'],
    ['资产负债率', pct(latest.debt_asset_ratio_pct), '金融企业不适用普通标准'],
    ['流动比率', multiple(latest.current_ratio), '需结合资产变现与债务期限'],
    ['PE / PB', `${fmt(quote.pe_dynamic)} / ${fmt(quote.pb)}`, '区分行业与盈利周期'],
    ['PS（年化参考）', multiple(scores.good_price.metrics.ps_ratio), '总市值 ÷ 简单年化营收'],
    ['最新股息率', pct(latest.dividend_yield_pct), '高分红需核查持续性'],
    ['一年最大回撤', pct(historical.max_drawdown_1y_pct), '历史极端损失不代表未来上限'],
    ['换手率', pct(quote.turnover_pct), '辅助观察流动性与交易热度'],
    ['20 日平均成交额', money(historical.average_amount_20d), '结合流通市值和买卖价差']
  ];
  const metricGrid = $('core-metric-grid');
  metricGrid.replaceChildren();
  metrics.forEach(([label, value, note]) => {
    const item = document.createElement('div');
    item.className = 'core-metric-item';
    item.append(node('span', label), node('strong', value), node('small', note));
    metricGrid.append(item);
  });
  const flags = $('red-flag-list');
  flags.replaceChildren();
  if (!scores.red_flags.length) {
    flags.append(node('p', '当前规则未触发财务或公告红旗；这不代表不存在风险，仍需检查原文和附注。', 'small-note'));
  }
  scores.red_flags.forEach(flag => {
    const item = document.createElement('article');
    item.className = `red-flag-item ${flag.severity === '高' ? 'high' : flag.severity === '观察' ? 'observe' : ''}`;
    item.append(node('strong', `${flag.severity} · ${flag.title}`), node('p', flag.detail));
    if (flag.veto) item.append(node('span', '风险否决项'));
    flags.append(item);
  });
}

const researchChecks = Array.from(document.querySelectorAll('[data-research-check]'));
researchChecks.forEach(check => check.addEventListener('change', () => {
  const completed = researchChecks.filter(item => item.checked).length;
  text('research-progress', `${completed} / ${researchChecks.length}`);
}));

function renderHistory(data) {
  const history = data.historical;
  text('history-return-20', pct(history.return_20d_pct));
  text('history-return-60', pct(history.return_60d_pct));
  text('history-return-244', pct(history.return_244d_pct));
  text('history-drawdown', pct(history.max_drawdown_1y_pct));
  text('history-position', `${fmt(history.position_52w_pct)}%`);
  text('history-period', `${history.period_start} 至 ${history.period_end} · ${history.bars} 个交易日`);
  text('history-high', fmt(history.high_52w));
  text('history-low', fmt(history.low_52w));
  text('history-volatility', `${fmt(history.annual_volatility_1y_pct)}%`);
  text('history-up-days', `${fmt(history.up_days_60d_pct)}%`);
  text('history-best', pct(history.best_day_pct));
  text('history-worst', pct(history.worst_day_pct));
  text('yearly-note', history.yearly_note);
  const bars = $('yearly-bars');
  bars.replaceChildren();
  history.yearly_returns.forEach(item => {
    const container = document.createElement('div');
    container.className = 'year-item';
    const track = document.createElement('div');
    track.className = 'year-track';
    const fill = document.createElement('div');
    fill.className = `year-fill ${item.return_pct < 0 ? 'negative' : ''}`;
    fill.style.height = `${Math.min(100, Math.max(4, Math.abs(item.return_pct) * 2.5))}%`;
    track.append(fill);
    container.append(node('div', pct(item.return_pct), `year-value ${directionClass(item.return_pct)}`), track, node('div', item.year, 'year-label'));
    bars.append(container);
  });
  renderFinancials('history-finance-body', data.financials);
  setList('risk-list', data.risks);
  renderChart('history-chart', data.history, [], false);
}

function renderFinancials(id, reports) {
  const body = $(id);
  body.replaceChildren();
  if (!reports.length) {
    const row = document.createElement('tr');
    const cell = node('td', '暂无财务数据');
    cell.colSpan = 7;
    row.append(cell);
    body.append(row);
    return;
  }
  reports.forEach(report => {
    const row = document.createElement('tr');
    row.append(
      node('td', report.report_type), node('td', money(report.revenue)), node('td', money(report.net_profit)),
      node('td', pct(report.revenue_yoy_pct), directionClass(report.revenue_yoy_pct)),
      node('td', pct(report.net_profit_yoy_pct), directionClass(report.net_profit_yoy_pct)),
      node('td', fmt(report.eps)), node('td', report.roe_pct === null || report.roe_pct === undefined ? '—' : `${fmt(report.roe_pct)}%`)
    );
    body.append(row);
  });
}

function renderAnnouncements(announcements) {
  const list = $('announcement-list');
  list.replaceChildren();
  if (!announcements.length) {
    list.append(node('p', '近90日暂无获取到公告，事件项按中性处理。', 'small-note'));
    return;
  }
  announcements.forEach(announcement => {
    const item = document.createElement('div');
    item.className = 'announcement-item';
    const impactClass = announcement.impact === '利好' ? 'positive' : announcement.impact === '风险' ? 'negative' : '';
    const link = node('a', announcement.title);
    link.href = announcement.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    item.append(node('span', announcement.date, 'announcement-date'), node('span', announcement.impact, `impact ${impactClass}`), link);
    list.append(item);
  });
}

function renderAI(research) {
  if (!research.enabled) {
    text('ai-headline', '多 Agent 研究未运行');
    text('ai-model', 'KIMI');
    text('ai-run-status', '未启用');
    text('ai-summary', research.summary);
    text('ai-conclusion', '勾选顶部“同时运行 Kimi 多 Agent”，重新分析即可生成五专家和主编结论。');
    setList('ai-strengths', []);
    setList('ai-risks', []);
    $('ai-watch').replaceChildren();
    $('agent-grid').replaceChildren();
    text('ai-usage', '');
    text('ai-run-id', '');
    text('ai-notice', '规则评分、历史分析和筛选功能仍可正常使用。');
    return;
  }
  text('ai-headline', research.headline);
  text('ai-model', `${research.model} · ${research.reasoning_effort}`);
  text('ai-run-status', research.status === 'completed' ? '全部完成' : research.status === 'partial' ? '部分完成' : '已降级');
  $('ai-run-status').className = `agent-status ${research.status === 'completed' ? 'completed' : 'failed'}`;
  text('ai-summary', research.summary);
  text('ai-conclusion', research.research_conclusion);
  setList('ai-strengths', research.strengths);
  setList('ai-risks', research.risks);
  const watch = $('ai-watch');
  watch.replaceChildren();
  research.watch_items.forEach(item => {
    const li = document.createElement('li');
    li.append(node('strong', item.topic), document.createTextNode(`：${item.reason}`));
    watch.append(li);
  });
  const grid = $('agent-grid');
  grid.replaceChildren();
  research.agents.forEach(agent => {
    const card = document.createElement('article');
    card.className = 'agent-card';
    card.append(
      node('span', agent.status === 'completed' ? '已完成' : '已降级', `agent-status ${agent.status}`),
      node('strong', agent.title),
      node('span', `${(agent.duration_ms / 1000).toFixed(1)}s · ${agent.usage.total_tokens} tokens`, 'agent-meta'),
      node('p', agent.summary), node('p', agent.evidence[0] || agent.error || '')
    );
    grid.append(card);
  });
  text('ai-usage', `总 Token ${research.usage.total_tokens} · 输入 ${research.usage.prompt_tokens} · 输出 ${research.usage.completion_tokens}`);
  text('ai-run-id', `Run ${research.run_id} · 快照 ${research.input_digest.slice(0, 12)}${research.cache_hit ? ' · 缓存命中' : ''}`);
  text('ai-notice', research.notice);
}

function renderChart(id, history, days, includeForecast) {
  const svg = $(id);
  const width = 1000, height = 300, left = 30, right = 68, top = 20, bottom = 38;
  const historical = history.map(item => Number(item.close));
  const predicted = includeForecast ? days.map(item => Number(item.predicted_close)) : [];
  const allValues = historical.concat(predicted);
  if (includeForecast) days.forEach(day => allValues.push(Number(day.lower_close_80), Number(day.upper_close_80)));
  const minimum = Math.min(...allValues), maximum = Math.max(...allValues);
  const padding = Math.max((maximum - minimum) * .08, maximum * .01);
  const low = minimum - padding, high = maximum + padding;
  const total = historical.length + predicted.length;
  const x = index => left + index * (width - left - right) / Math.max(1, total - 1);
  const y = value => top + (high - value) * (height - top - bottom) / (high - low);
  const historyPath = historical.map((value, index) => `${index ? 'L' : 'M'}${x(index).toFixed(2)},${y(value).toFixed(2)}`).join(' ');
  const gradientId = `${id}-band`;
  let markup = `<defs><linearGradient id="${gradientId}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e47245" stop-opacity=".22"/><stop offset="1" stop-color="#e47245" stop-opacity=".04"/></linearGradient></defs>`;
  for (let index = 0; index < 5; index++) {
    const value = low + (high - low) * index / 4;
    const py = y(value);
    markup += `<line x1="${left}" y1="${py}" x2="${width - right}" y2="${py}" stroke="#e7ebe8"/>`;
    markup += `<text x="${width - right + 10}" y="${py + 4}" fill="#7a8586" font-size="11">${value.toFixed(2)}</text>`;
  }
  if (includeForecast) {
    const splitX = x(historical.length - 1);
    const forecastValues = [historical[historical.length - 1]].concat(predicted);
    const forecastPath = forecastValues.map((value, index) => `${index ? 'L' : 'M'}${x(historical.length - 1 + index).toFixed(2)},${y(value).toFixed(2)}`).join(' ');
    const upper = days.map((day, index) => `${index ? 'L' : 'M'}${x(historical.length + index).toFixed(2)},${y(Number(day.upper_close_80)).toFixed(2)}`).join(' ');
    const lower = days.slice().reverse().map((day, reverseIndex) => {
      const index = days.length - 1 - reverseIndex;
      return `L${x(historical.length + index).toFixed(2)},${y(Number(day.lower_close_80)).toFixed(2)}`;
    }).join(' ');
    markup += `<line x1="${splitX}" y1="${top}" x2="${splitX}" y2="${height - bottom}" stroke="#bcc7c5" stroke-dasharray="4 5"/>`;
    markup += `<path d="${upper} ${lower} Z" fill="url(#${gradientId})"/>`;
    markup += `<path d="${historyPath}" fill="none" stroke="#287284" stroke-width="2.3" stroke-linejoin="round"/>`;
    markup += `<path d="${forecastPath}" fill="none" stroke="#e47245" stroke-width="2.5" stroke-dasharray="6 4"/>`;
    days.forEach((day, index) => {
      const px = x(historical.length + index), py = y(Number(day.predicted_close));
      markup += `<circle cx="${px}" cy="${py}" r="4" fill="#fff" stroke="#e47245" stroke-width="2"/>`;
      markup += `<text x="${px}" y="${height - 13}" text-anchor="middle" fill="#7a8586" font-size="11">${day.date.slice(5)}</text>`;
    });
  } else {
    markup += `<path d="${historyPath}" fill="none" stroke="#287284" stroke-width="2.3" stroke-linejoin="round"/>`;
  }
  markup += `<text x="${left}" y="${height - 13}" fill="#7a8586" font-size="11">${history[0].date}</text>`;
  markup += `<text x="${width - right}" y="${height - 13}" text-anchor="end" fill="#7a8586" font-size="11">${history[history.length - 1].date}</text>`;
  svg.innerHTML = markup;
}

function setChatRunning(running) {
  chatRunning = running;
  $('chat-send').disabled = running || !chatAvailable;
  $('chat-new').disabled = running;
  $('chat-message').disabled = running;
  $('chat-query').disabled = running || Boolean(chatSessionId);
  $('chat-running').classList.toggle('hidden', !running);
  $('chat-send').textContent = running ? '讨论中…' : '发送';
}

function saveChatSession() {
  try {
    if (chatSessionId) localStorage.setItem('fam.chat.session', chatSessionId);
    else localStorage.removeItem('fam.chat.session');
  } catch (error) {}
}

function traceStatusLabel(status) {
  return ({ running: '进行中', completed: '已完成', degraded: '部分降级', failed: '失败' })[status] || '等待提问';
}

function updateTraceOptions() {
  const select = $('trace-turn-select');
  select.replaceChildren();
  let index = 0;
  chatTraces.forEach((record, id) => {
    index += 1;
    const option = node('option', `第 ${index} 轮 · ${record.question.slice(0, 24)}`);
    option.value = id;
    select.append(option);
  });
  select.classList.toggle('hidden', !chatTraces.size);
  if (activeTraceId) select.value = activeTraceId;
}

function renderTrace() {
  const list = $('trace-events');
  const record = chatTraces.get(activeTraceId);
  list.replaceChildren();
  if (!record) {
    list.append(node('li', '发送问题后，这里会实时显示每个 Agent 的调用与消息链路。', 'trace-empty'));
    text('trace-id', '');
    text('trace-usage', '');
    text('trace-status', '等待提问');
    $('trace-status').className = 'agent-status';
    return;
  }
  text('trace-id', `${record.id} · Session ${chatSessionId || ''}`);
  text('trace-status', traceStatusLabel(record.status));
  $('trace-status').className = `agent-status ${record.status}`;
  const usage = record.usage;
  text('trace-usage', usage ? `${usage.total_tokens} tokens${record.duration_ms ? ` · ${(record.duration_ms / 1000).toFixed(1)}s` : ''}` : '等待用量统计');
  record.events.forEach(event => {
    const item = document.createElement('li');
    item.className = `trace-event ${event.status || ''}`;
    const head = document.createElement('div');
    head.className = 'trace-event-head';
    head.append(node('strong', `${event.sequence}. ${event.name || event.event}`), node('time', (event.timestamp || '').slice(11, 23)));
    item.append(head);
    if (event.span_id) {
      const span = node('span', `${event.span_id} · 父 ${event.parent_span_id || '—'}`, 'trace-event-meta');
      span.title = `Trace ${event.trace_id}\nSpan ${event.span_id}\nParent ${event.parent_span_id || '—'}`;
      item.append(span);
    }
    const metrics = [];
    if (event.duration_ms !== undefined) metrics.push(`${(event.duration_ms / 1000).toFixed(2)}s`);
    if (event.usage) metrics.push(`${event.usage.total_tokens} tokens`);
    if (event.request_id) metrics.push(`请求 ${event.request_id}`);
    if (metrics.length) item.append(node('span', metrics.join(' · '), 'trace-event-meta'));
    if (event.detail) item.append(node('p', event.detail, `trace-event-detail${event.event === 'agent/message' ? ' agent-output' : ''}`));
    list.append(item);
  });
  list.scrollTop = list.scrollHeight;
}

function appendChatMessage(message) {
  if (renderedChatMessages.has(message.id)) return;
  renderedChatMessages.add(message.id);
  $('chat-welcome').classList.add('hidden');
  const article = document.createElement('article');
  const isLead = message.agent_id === 'lead';
  article.className = `chat-message ${message.role === 'user' ? 'user' : ''} ${isLead ? 'lead' : ''} ${message.status === 'failed' ? 'failed' : ''}`;
  const head = document.createElement('div');
  head.className = 'chat-message-head';
  head.append(node('strong', message.agent), node('span', `→ ${message.target}`, 'chat-target'));
  if (message.status === 'failed') head.append(node('span', '已降级', 'agent-status failed'));
  head.append(node('time', (message.timestamp || '').slice(11, 19)));
  article.append(head, node('p', message.content));
  if (message.key_points && message.key_points.length) {
    const list = document.createElement('ul');
    message.key_points.forEach(point => list.append(node('li', point)));
    article.append(list);
  }
  if (message.handoff && message.role !== 'user') {
    article.append(node('div', `${isLead ? '风险边界' : `交接给 ${message.target}`}：${message.handoff}`, 'chat-handoff'));
  }
  $('chat-messages').append(article);
  $('chat-messages').scrollTop = $('chat-messages').scrollHeight;
}

function handleChatEvent(data) {
  if (data.type === 'trace') {
    const event = data.trace;
    if (event.event === 'trace/started') {
      chatSessionId = event.session_id;
      activeTraceId = event.trace_id;
      chatTraces.set(event.trace_id, {
        id: event.trace_id,
        question: event.detail,
        status: 'running',
        events: [],
        usage: null,
        duration_ms: 0
      });
      $('chat-query').value = event.data.stock_query;
      $('chat-query').disabled = true;
      text('chat-stock', `${event.data.stock_query} · 多 Agent 会话`);
      text('chat-session-status', `Session ${event.session_id}`);
      saveChatSession();
      updateTraceOptions();
    }
    if (!chatTraces.has(event.trace_id)) {
      chatTraces.set(event.trace_id, { id: event.trace_id, question: event.name, status: 'running', events: [], usage: null });
    }
    const record = chatTraces.get(event.trace_id);
    record.events.push(event);
    if (event.event === 'trace/completed' || event.event === 'trace/failed') {
      record.status = event.status;
      record.usage = event.usage || record.usage;
      record.duration_ms = event.duration_ms || 0;
    }
    if (event.message) appendChatMessage(event.message);
    if (event.agent_id === 'data' && event.event === 'span/completed') text('chat-stock', event.detail);
    if (event.trace_id === activeTraceId) renderTrace();
    return;
  }
  if (data.type === 'done') {
    const record = chatTraces.get(data.trace_id);
    if (record) {
      record.status = data.status;
      record.usage = data.usage;
      record.duration_ms = data.duration_ms;
    }
    text('chat-session-status', `${data.status === 'completed' ? '本轮完成' : '本轮部分降级'} · ${(data.duration_ms / 1000).toFixed(1)}s · 可继续追问`);
    renderTrace();
    return;
  }
  if (data.type === 'error') {
    text('chat-error', data.error || '多 Agent 对话失败');
    if (data.trace_id && chatTraces.has(data.trace_id)) {
      chatTraces.get(data.trace_id).status = 'failed';
      renderTrace();
    }
  }
}

async function consumeChatStream(response) {
  if (!response.body) throw new Error('当前浏览器不支持流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    lines.filter(Boolean).forEach(line => handleChatEvent(JSON.parse(line)));
    if (done) break;
  }
  if (buffer.trim()) handleChatEvent(JSON.parse(buffer));
}

$('chat-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (chatRunning) return;
  const message = $('chat-message').value.trim();
  const query = $('chat-query').value.trim();
  if (!message || (!chatSessionId && !query)) return;
  text('chat-error', '');
  setChatRunning(true);
  $('chat-message').value = '';
  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, query, session_id: chatSessionId })
    });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || '对话请求失败');
    }
    await consumeChatStream(response);
  } catch (error) {
    text('chat-error', error.message || '网络连接失败，请稍后重试');
    $('chat-message').value = $('chat-message').value || message;
  } finally {
    setChatRunning(false);
  }
});

$('chat-message').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $('chat-form').requestSubmit();
  }
});

document.querySelectorAll('[data-chat-prompt]').forEach(button => button.addEventListener('click', () => {
  $('chat-message').value = button.dataset.chatPrompt;
  $('chat-message').focus();
}));

$('chat-new').addEventListener('click', () => {
  chatSessionId = null;
  activeTraceId = null;
  chatTraces.clear();
  renderedChatMessages.clear();
  saveChatSession();
  $('chat-messages').replaceChildren();
  $('chat-welcome').classList.remove('hidden');
  $('chat-query').disabled = false;
  $('chat-message').value = '';
  text('chat-stock', '新建多 Agent 研究会话');
  text('chat-session-status', '主持分派，专家依次回应，风险红队质疑，主编综合回复');
  text('chat-error', '');
  updateTraceOptions();
  renderTrace();
  $('chat-query').focus();
});

$('trace-turn-select').addEventListener('change', event => {
  activeTraceId = event.target.value;
  renderTrace();
});

$('trace-export').addEventListener('click', () => {
  const record = chatTraces.get(activeTraceId);
  if (!record) return;
  const payload = {
    session_id: chatSessionId,
    trace: record,
    notice: '仅包含公开 Agent 消息与调用元数据，不包含模型思维链'
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${record.id}.json`;
  link.click();
  URL.revokeObjectURL(url);
});

async function restoreChatSession() {
  let saved = null;
  try { saved = localStorage.getItem('fam.chat.session'); } catch (error) {}
  if (!saved) return;
  try {
    const response = await fetch(`/api/chat/session?id=${encodeURIComponent(saved)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '会话恢复失败');
    chatSessionId = data.id;
    $('chat-query').value = data.query;
    $('chat-query').disabled = true;
    text('chat-stock', data.stock ? `${data.stock.name}（${data.stock.code}）· 多 Agent 会话` : `${data.query} · 多 Agent 会话`);
    text('chat-session-status', `Session ${data.id} · 已恢复 ${data.turns.length} 轮对话`);
    data.turns.forEach(turn => {
      chatTraces.set(turn.trace_id, {
        id: turn.trace_id,
        question: turn.question,
        status: turn.status,
        usage: turn.usage,
        duration_ms: turn.duration_ms,
        events: turn.events
      });
      turn.messages.forEach(appendChatMessage);
      activeTraceId = turn.trace_id;
    });
    updateTraceOptions();
    renderTrace();
  } catch (error) {
    chatSessionId = null;
    saveChatSession();
    text('chat-error', '原会话已失效，请新建会话');
  }
}

restoreChatSession();

$('screen-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('screen-submit').disabled = true;
  $('screen-submit').textContent = '加载中…';
  $('screen-loading').classList.remove('hidden');
  $('screen-error').classList.add('hidden');
  try {
    const response = await fetch('/api/screen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ limit: Number($('screen-limit').value) })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '股票池加载失败');
    screenData = data;
    $('explorer-detail').classList.add('hidden');
    applyFilters();
    text('screen-meta', `${data.meta.generated_at.replace('T', ' ')} · 请求 ${data.meta.requested} 只，有效 ${data.meta.screened} 只，失败 ${data.meta.failed} 只。${data.meta.universe_note}`);
    const qualified = $('qualified-list');
    qualified.replaceChildren();
    data.qualified.forEach(item => qualified.append(node('span', `三好共振 · ${item.stock.name} ${fmt(item.scores.overall_score, 1)}`)));
    $('screen-result').classList.remove('hidden');
  } catch (error) {
    $('screen-error').textContent = error.message || '网络连接失败';
    $('screen-error').classList.remove('hidden');
  } finally {
    $('screen-submit').disabled = false;
    $('screen-submit').textContent = '加载股票池';
    $('screen-loading').classList.add('hidden');
  }
});

const accessors = {
  overall: item => item.scores.overall_score,
  good_stock: item => item.scores.good_stock.score,
  good_price: item => item.scores.good_price.score,
  good_timing: item => item.scores.good_timing.score,
  pe: item => item.quote.pe_dynamic,
  pb: item => item.quote.pb,
  market_cap: item => item.quote.total_market_value,
  revenue_growth: item => item.earnings?.revenue_yoy_pct ?? null,
  profit_growth: item => item.earnings?.net_profit_yoy_pct ?? null,
  roe: item => item.scores.good_stock.metrics.annualized_roe_pct,
  momentum: item => item.technical.momentum_20d_pct,
  rsi: item => item.technical.rsi14
};

function applyFilters() {
  if (!screenData) return;
  let items = [...screenData.items];
  const active = [];
  const query = $('filter-query').value.trim().toLowerCase();
  if (query) {
    items = items.filter(item => `${item.stock.name}${item.stock.code}${item.quote.industry}`.toLowerCase().includes(query));
    active.push(`查询:${query}`);
  }
  const industry = $('filter-industry').value;
  if (industry) {
    items = items.filter(item => item.scores.industry_style === industry);
    active.push(`行业:${industry}`);
  }
  const numericFilters = [
    ['filter-min-overall', '综合≥', accessors.overall, 'min'],
    ['filter-min-stock', '好股票≥', accessors.good_stock, 'min'],
    ['filter-min-price', '好价格≥', accessors.good_price, 'min'],
    ['filter-min-timing', '好时机≥', accessors.good_timing, 'min'],
    ['filter-pe-min', 'PE≥', accessors.pe, 'min'], ['filter-pe-max', 'PE≤', accessors.pe, 'max'],
    ['filter-pb-max', 'PB≤', accessors.pb, 'max'],
    ['filter-revenue-min', '营收≥', accessors.revenue_growth, 'min'],
    ['filter-profit-min', '净利≥', accessors.profit_growth, 'min'],
    ['filter-roe-min', 'ROE≥', accessors.roe, 'min'],
    ['filter-momentum-min', '动量≥', accessors.momentum, 'min'], ['filter-momentum-max', '动量≤', accessors.momentum, 'max'],
    ['filter-rsi-min', 'RSI≥', accessors.rsi, 'min'], ['filter-rsi-max', 'RSI≤', accessors.rsi, 'max']
  ];
  numericFilters.forEach(([id, label, accessor, mode]) => {
    const raw = $(id).value;
    if (raw === '') return;
    const threshold = Number(raw);
    items = items.filter(item => {
      const value = accessor(item);
      return value !== null && value !== undefined && (mode === 'min' ? value >= threshold : value <= threshold);
    });
    active.push(`${label}${threshold}`);
  });
  const announcement = $('filter-announcement').value;
  if (announcement) {
    items = items.filter(item => {
      const review = item.scores.announcement_review;
      if (announcement === 'positive') return review.positive_count > 0;
      if (announcement === 'risk') return review.negative_count > 0;
      if (announcement === 'no-risk') return review.negative_count === 0;
      return review.positive_count === 0 && review.negative_count === 0;
    });
    active.push(`公告:${$( 'filter-announcement').selectedOptions[0].textContent}`);
  }
  const sort = $('sort-by').value;
  const direction = $('sort-order').value === 'asc' ? 1 : -1;
  const accessor = accessors[sort];
  items.sort((a, b) => {
    const av = accessor(a), bv = accessor(b);
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    return (av - bv) * direction;
  });
  filteredItems = items;
  text('filter-summary', `股票池 ${screenData.items.length} 只，符合条件 ${items.length} 只`);
  const chips = $('active-filters');
  chips.replaceChildren();
  active.push(`排序:${$('sort-by').selectedOptions[0].textContent}${direction === 1 ? '↑' : '↓'}`);
  active.forEach(label => chips.append(node('span', label)));
  renderExplorerTable();
}

$('filter-form').addEventListener('submit', event => {
  event.preventDefault();
  if (!screenData) {
    $('screen-error').textContent = '请先加载股票池，再应用筛选条件';
    $('screen-error').classList.remove('hidden');
    return;
  }
  $('screen-error').classList.add('hidden');
  applyFilters();
});

$('filter-reset').addEventListener('click', () => {
  document.querySelectorAll('#filter-form input').forEach(input => { input.value = ''; });
  $('filter-industry').value = '';
  $('filter-announcement').value = '';
  $('sort-by').value = viewConfig[currentView].sort;
  $('sort-order').value = 'desc';
  applyFilters();
});

function explorerColumns() {
  const common = [
    { label: '股票', value: item => `${item.stock.name} ${item.stock.code}` },
    { label: '行业', value: item => item.quote.industry },
    { label: '现价', value: item => fmt(item.quote.price) }
  ];
  if (currentView === 'stock') return common.concat([
    { label: '好股票', value: item => fmt(item.scores.good_stock.score, 1), score: true },
    { label: '年化ROE', value: item => pct(accessors.roe(item)) },
    { label: '营收同比', value: item => pct(accessors.revenue_growth(item)) },
    { label: '净利同比', value: item => pct(accessors.profit_growth(item)) },
    { label: '现金/EPS', value: item => fmt(item.scores.good_stock.metrics.cash_conversion) },
    { label: '综合', value: item => fmt(item.scores.overall_score, 1) }
  ]);
  if (currentView === 'price') return common.concat([
    { label: '好价格', value: item => fmt(item.scores.good_price.score, 1), score: true },
    { label: 'PE', value: item => fmt(item.quote.pe_dynamic) }, { label: 'PB', value: item => fmt(item.quote.pb) },
    { label: '估值区间', value: item => item.scores.good_price.metrics.value_low === null ? '—' : `${fmt(item.scores.good_price.metrics.value_low)}~${fmt(item.scores.good_price.metrics.value_high)}` },
    { label: '综合', value: item => fmt(item.scores.overall_score, 1) }
  ]);
  if (currentView === 'factors') return common.concat([
    { label: '七维综合', value: item => fmt(item.scores.overall_score, 1), score: true },
    { label: '有效维度', value: item => `${item.scores.seven_factor.coverage.available_dimensions}/7` },
    { label: '现金/净利', value: item => fmt(item.scores.seven_factor.dimensions.find(dimension => dimension.id === 'cash').metrics.operating_cash_to_profit) },
    { label: '负债率', value: item => pct(item.scores.seven_factor.dimensions.find(dimension => dimension.id === 'safety').metrics.debt_asset_ratio_pct) },
    { label: '风险状态', value: item => item.scores.seven_factor.veto.triggered ? '风险否决' : `${item.scores.red_flags.length} 项观察` }
  ]);
  return common.concat([
    { label: '好时机', value: item => fmt(item.scores.good_timing.score, 1), score: true },
    { label: '20日动量', value: item => pct(item.technical.momentum_20d_pct) },
    { label: 'RSI', value: item => fmt(item.technical.rsi14) },
    { label: '板块20日', value: item => pct(item.sector?.momentum_20d_pct ?? null) },
    { label: '公告事/风', value: item => `${item.scores.announcement_review.positive_count}/${item.scores.announcement_review.negative_count}` },
    { label: '综合', value: item => fmt(item.scores.overall_score, 1) }
  ]);
}

function renderExplorerTable() {
  const columns = explorerColumns();
  const head = $('explorer-head');
  const body = $('explorer-body');
  head.replaceChildren();
  body.replaceChildren();
  const headerRow = document.createElement('tr');
  columns.forEach(column => headerRow.append(node('th', column.label)));
  headerRow.append(node('th', '研究结论'), node('th', '操作'));
  head.append(headerRow);
  if (!filteredItems.length) {
    const row = document.createElement('tr');
    const cell = node('td', '没有符合当前条件的股票，请调整筛选条件');
    cell.colSpan = columns.length + 2;
    row.append(cell);
    body.append(row);
    return;
  }
  filteredItems.forEach(item => {
    const row = document.createElement('tr');
    columns.forEach(column => row.append(node('td', column.value(item), column.score ? 'score-cell' : '')));
    row.append(node('td', item.scores.verdict));
    const detail = node('button', '依据', 'table-link');
    detail.type = 'button';
    detail.dataset.detail = item.stock.code;
    const analyze = node('button', '分析', 'table-link');
    analyze.type = 'button';
    analyze.dataset.analyze = item.stock.code;
    const actions = document.createElement('td');
    actions.append(detail, document.createTextNode(' · '), analyze);
    row.append(actions);
    body.append(row);
  });
}

$('explorer-body').addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.analyze) {
    $('query').value = button.dataset.analyze;
    $('search-form').requestSubmit();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } else if (button.dataset.detail) {
    const item = filteredItems.find(candidate => candidate.stock.code === button.dataset.detail);
    if (item) showExplorerDetail(item);
  }
});

function showExplorerDetail(item) {
  text('detail-title', `${item.stock.name}（${item.stock.code}）评分依据`);
  text('detail-note', `${item.scores.industry_style}行业模板 · ${item.fundamental.summary} · ${item.scores.method_note}`);
  setList('detail-stock', item.scores.good_stock.reasons);
  setList('detail-price', item.scores.good_price.reasons);
  setList('detail-timing', item.scores.good_timing.reasons);
  $('explorer-detail').classList.remove('hidden');
  $('explorer-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

$('detail-close').addEventListener('click', () => $('explorer-detail').classList.add('hidden'));

$('export-btn').addEventListener('click', () => {
  if (!screenData || !filteredItems.length) return;
  const columns = explorerColumns();
  const escape = value => `"${String(value).replaceAll('"', '""')}"`;
  const lines = [columns.map(column => escape(column.label)).join(',')];
  filteredItems.forEach(item => lines.push(columns.map(column => escape(column.value(item))).join(',')));
  const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${currentView}_stock_screen.csv`;
  link.click();
  URL.revokeObjectURL(url);
});
