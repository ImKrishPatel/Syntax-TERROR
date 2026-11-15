// Ties everything together.
//
// The idea is simple: there is ONE object called `state` that says what the
// user has selected (area, years, month, ...). Whenever it changes we call
// render(), which redraws every part of the page from the state. The state is
// also written into the page address, so a link opens the exact same view.

import { CONFIG, MONTHS } from './config.js'
import { data, loadHotspots, loadJson, monthlyTotals, cellTotals, rowsAsCsv, monthIndex, cellsInArea } from './data.js'
import { buildTable, monthVerdict, overviewVerdict, formatValue, formatNumber, formatPower, formatScore, LEVEL_TEXT } from './stats.js'
import { SCORE_STEPS } from './colors.js'
import { createMap } from './map.js'
import { drawCalendar, markPlaying } from './calendar.js'
import { drawSeasonChart, drawForecastChart, monthShape } from './charts.js'
import { createListeningPanel } from './sound.js'
import { playCalendar, stopCalendar, isPlaying } from './sweep.js'

const $ = id => document.getElementById(id)

const state = {
  area: [...CONFIG.defaultArea],   // [south, west, north, east]
  fromYear: 0,
  toYear: 0,
  month: null,                     // '2023-05' when one month is selected, otherwise null
  metric: 'count',                 // 'count' (hotspots) or 'frp' (fire energy)
  mode: 'amount',                  // calendar colours: 'amount' or 'score'
  layer: 'hotspots',               // map layer: 'hotspots' or 'regimes'
  mapYear: null,                   // one year shown on the map (time slider), or null for the selected years
  hasArea: false,                  // false until a place is searched or picked: the map is a plain globe
}
let page = 'home'                  // 'home' or 'map'
let feature = null                 // open feature window: 'trends', 'anomalies', 'patterns', 'listen', 'about'

let listening = null
let mapView = null
let table = null        // the calendar numbers for the current area and years
let regimes = null      // { geojson, summary } once loaded
let playingAt = null    // { year, month } while the map is playing through the record

// ---------------------------------------------------------------------------
// State <-> page address
// ---------------------------------------------------------------------------
function readAddress() {
  const params = new URLSearchParams(location.hash.slice(1))
  const area = (params.get('area') || '').split(',').map(Number)
  state.hasArea = area.length === 4 && area.every(v => Number.isFinite(v)) && area[0] < area[2] && area[1] < area[3]
  if (state.hasArea) state.area = area
  const years = (params.get('years') || '').split('-').map(Number)
  if (years.length === 2 && years[0] >= data.firstYear && years[1] <= data.lastYear && years[0] <= years[1]) {
    state.fromYear = years[0]; state.toYear = years[1]
  }
  const month = params.get('month')
  state.month = month && monthIndex(month) >= 0 ? month : null
  if (params.get('measure') === 'frp') state.metric = 'frp'
  if (params.get('colour') === 'score') state.mode = 'score'
  if (params.get('layer') === 'regimes') state.layer = 'regimes'
}

function writeAddress() {
  const params = new URLSearchParams()
  if (!state.hasArea) {
    try { history.replaceState(null, '', page === 'map' ? '#page=map' : location.pathname + location.search) } catch (err) { /* fine */ }
    return
  }
  params.set('area', state.area.join(','))
  params.set('years', `${state.fromYear}-${state.toYear}`)
  if (state.month) params.set('month', state.month)
  if (state.metric === 'frp') params.set('measure', 'frp')
  if (state.mode === 'score') params.set('colour', 'score')
  if (state.layer === 'regimes') params.set('layer', 'regimes')
  try {
    history.replaceState(null, '', '#' + params.toString().replace(/%2C/g, ','))
  } catch (err) { /* some embedded viewers do not allow this; the app still works */ }
}

function update(changes, fromAudio = false) {
  if (!fromAudio) listening?.stop()
  if (changes.area) state.hasArea = true   // any chosen area (search, example, map click, typed edges) ends the plain globe
  Object.assign(state, changes)
  // a selected month must be inside the selected years
  if (state.month) {
    const y = Number(state.month.slice(0, 4))
    if (y < state.fromYear || y > state.toYear) state.month = null
  }
  writeAddress()
  render()
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function areaName() {
  const preset = CONFIG.presets.find(p => p.box.every((v, i) => v === state.area[i]))
  return preset ? preset.name : 'Custom area'
}
function degrees(v, positive, negative) {
  return `${Math.abs(v)}°${v < 0 ? negative : positive}`
}
function areaText() {
  const [s, w, n, e] = state.area
  return `${degrees(s, 'N', 'S')} to ${degrees(n, 'N', 'S')}, ${degrees(w, 'E', 'W')} to ${degrees(e, 'E', 'W')}`
}
function focus() {
  if (!state.month) return null
  return { year: Number(state.month.slice(0, 4)), month: Number(state.month.slice(5, 7)) - 1 }
}
function monthKey(year, month) { return `${year}-${String(month + 1).padStart(2, '0')}` }
function setPressed(selector, attribute, value) {
  document.querySelectorAll(selector).forEach(button => {
    button.setAttribute('aria-pressed', button.dataset[attribute] === value ? 'true' : 'false')
  })
}

// ---------------------------------------------------------------------------
// Render: redraw everything from the state
// ---------------------------------------------------------------------------
function render() {
  const picked = focus()
  const totals = monthlyTotals(state.area)
  table = buildTable(totals, state.metric, state.fromYear, state.toYear)
  const cellsInside = cellsInArea(state.area).reduce((a, b) => a + b, 0)

  // --- controls show the current state ---
  $('areaError').hidden = true
  document.querySelectorAll('#presetChips button').forEach(b => b.setAttribute('aria-pressed', String(b.textContent === areaName())))
  $('south').value = state.area[0]; $('west').value = state.area[1]
  $('north').value = state.area[2]; $('east').value = state.area[3]
  $('fromYear').value = state.fromYear; $('toYear').value = state.toYear
  setPressed('[data-metric]', 'metric', state.metric)
  setPressed('[data-mode]', 'mode', state.mode)
  setPressed('[data-layer]', 'layer', state.layer)

  // --- map: a plain globe until a place is chosen ---
  mapView.setPlain(!state.hasArea)
  document.body.classList.toggle('is-plain', !state.hasArea)
  renderFeatureWindow()
  mapView.setArea(state.area)
  const first = picked ? monthIndex(state.month) : Math.max(0, monthIndex(`${state.fromYear}-01`))
  const lastKey = `${state.toYear}-12`
  const last = picked ? first : (monthIndex(lastKey) >= 0 ? monthIndex(lastKey) : data.months.length - 1)
  const period = picked ? `${MONTHS[picked.month]} ${picked.year}` : `${state.fromYear} to ${state.toYear}`
  drawMapPeriod(picked)
  mapView.showLayer(state.layer)

  // --- verdict ---
  renderVerdict(picked, totals, cellsInside)

  // --- calendar ---
  drawCalendar($('calendar'), $('calendarLegend'), table, state.mode, picked, (year, month) => {
    const key = monthKey(year, month)
    update({ month: state.month === key ? null : key })   // selecting the same square again clears it
  })
  renderAnomalies()

  listening?.update(table, picked, JSON.stringify([state.area,state.fromYear,state.toYear,state.metric]), cellsInside > 0, state.mode)

  // --- season chart ---
  const overview = overviewVerdict(table)
  const chartYear = picked ? picked.year : (overview.bigYear || state.toYear)
  $('seasonTitle').textContent = `${chartYear} against a usual year`
  $('seasonSub').textContent = picked
    ? `${state.metric === 'frp' ? 'Fire energy' : 'Hotspots'} per month in the selected area.`
    : `${chartYear} is the biggest year in this area. Select any month in the calendar to see its year here.`
  $('seasonKeyYear').textContent = chartYear
  drawSeasonChart($('seasonChart'), table, chartYear)

  renderRegimes()

  // --- export text ---
  const csv = rowsAsCsv(state.area, first, last)
  $('exportText').textContent = csv.rows
    ? `${formatNumber(csv.rows)} rows: every grid cell and month with hotspots in ${areaName() === 'Custom area' ? 'the selected area' : areaName()}, ${period}.`
    : 'There are no hotspots in this area and period, so there is nothing to export.'
  $('downloadButton').disabled = $('copyCsvButton').disabled = !csv.rows
}

// what the map shows: the month being played, a picked month, one slider year, or the selected years
function drawMapPeriod(picked) {
  const range = (a, b) => {
    const i = monthIndex(a), j = monthIndex(b)
    return [i >= 0 ? i : 0, j >= 0 ? j : data.months.length - 1]
  }
  let first, last, period
  if (playingAt) {
    first = last = monthIndex(monthKey(playingAt.year, playingAt.month)); period = `${MONTHS[playingAt.month]} ${playingAt.year}`
  } else if (picked) {
    first = last = monthIndex(state.month); period = `${MONTHS[picked.month]} ${picked.year}`
  } else if (state.mapYear) {
    [first, last] = range(`${state.mapYear}-01`, `${state.mapYear}-12`); period = String(state.mapYear)
  } else {
    [first, last] = range(`${state.fromYear}-01`, `${state.toYear}-12`); period = `${state.fromYear} to ${state.toYear}`
  }
  if (first < 0) first = 0
  if (last < first) last = first
  mapView.setCells(cellTotals(first, last), state.metric, period)
  $('mapYearText').textContent = playingAt || picked || state.mapYear ? period : 'All years'
  const year = playingAt ? playingAt.year : picked ? picked.year : state.mapYear
  // satellite picture for the same period: mid-month of the shown month, or August of a shown year
  // with no month chosen: the area's biggest fire month (in the shown year, if one is shown)
  let showY = year, showM = playingAt ? playingAt.month : picked ? picked.month : null
  if (showM === null && table) {
    let best = -1
    table.years.forEach((y, r) => {
      if (year && y !== year) return
      for (let m = 0; m < 12; m++) { const v = table.value[r][m]; if (v > best) { best = v; showY = y; showM = m } }
    })
  }
  if (showM === null) { showY = year || state.toYear; showM = 7 }
  // while Play runs, keep the satellite picture still: reloading it every month made the map flicker
  if (!playingAt) mapView.setImageryDate(`${showY}-${String(showM + 1).padStart(2, '0')}-15`)
  $('mapYear').value = year ? year - data.firstYear + 1 : 0
}

// data confidence badge: real observations or model estimates
function confidenceBadge() {
  const est = table.years.filter((_, i) => table.yearEstimated[i] >= 0.5)
  if (est.length) {
    return `<span class="badge is-estimated" title="Before 2012 only the older MODIS sensor existed, so those years are converted by our model.">Partly estimated: ${est[0]} to ${est[est.length - 1]}</span>`
  }
  return `<span class="badge is-observed" title="Every number here comes straight from NASA satellite fire detections (${CONFIG.observedLabel || 'VIIRS'}).">Real satellite observations</span>`
}

function renderVerdict(picked, totals, cellsInside) {
  const el = $('verdict')
  const where = `<p class="verdict-where"><b>${areaName()}</b><span>${areaText()}</span>${cellsInside ? confidenceBadge() : ''}</p>`

  if (cellsInside === 0) {
    el.className = 'verdict level-none'
    el.innerHTML = where +
      '<h1 class="verdict-sentence">There is no data for this area yet.</h1>' +
      '<p class="verdict-help">No satellite detected a fire here, or the box is over the sea. Drag the box onto land, or search for a place.</p>'
    return
  }

  if (picked && !Number.isFinite(table.value[picked.year - table.years[0]]?.[picked.month])) {
    el.className = 'verdict level-none'
    el.innerHTML = where + `<h1 class="verdict-sentence">No data for ${MONTHS[picked.month]} ${picked.year}.</h1>` +
      '<p class="verdict-help">Missing observations do not mean zero activity.</p><button type="button" class="link" id="clearMonth">Back to all years</button>'
    $('clearMonth').onclick = () => update({ month: null })
    return
  }

  if (picked) {
    const v = monthVerdict(table, picked.year, picked.month)
    const i = monthIndex(state.month)
    const estimated = v.estimatedShare >= 0.5
    el.className = `verdict level-${v.level}`
    el.innerHTML = where +
      `<h1 class="verdict-sentence">${v.sentence}</h1>` +
      '<div class="verdict-side">' +
        `<p class="level"><i class="level-icon" aria-hidden="true"></i>${LEVEL_TEXT[v.level]}</p>` +
        `<p class="score"><span class="score-number">${v.level === 'none' ? 'n/a' : formatScore(v.score)}</span><span class="score-label">How unusual</span></p>` +
        scoreMeter(v.level === 'none' ? null : v.score) + '<p class="score-help">0 is normal · 2 or more is rare</p>' +
        '<button type="button" class="link" id="clearMonth">Back to all years</button>' +
      '</div>' +
      '<dl class="facts">' +
        `<div><dt>Hotspots</dt><dd>${formatNumber(totals.count[i])}</dd></div>` +
        `<div><dt>Fire energy</dt><dd>${formatPower(totals.frp[i])}</dd></div>` +
        `<div><dt>Usual ${MONTHS[picked.month]}</dt><dd>${formatValue(v.usual, state.metric)}</dd></div>` +
        `<div><dt>Rank</dt><dd>${v.level === 'none' ? 'n/a' : `${v.rank} of ${v.yearsCompared}`}</dd></div>` +
        `<div><dt>Record</dt><dd class="${estimated ? 'is-estimated' : 'is-observed'}">${Number.isNaN(v.estimatedShare) ? 'n/a' : estimated ? 'Estimated from MODIS' : (CONFIG.observedLabel || 'Real VIIRS')}</dd></div>` +
      '</dl>'
    $('clearMonth').onclick = () => update({ month: null })
    return
  }

  const o = overviewVerdict(table)
  const estimatedYears = table.years.filter((_, i) => table.yearEstimated[i] >= 0.5)
  el.className = 'verdict level-overview'
  let side = ''
  if (o.top && o.top.score >= CONFIG.unusualSigma) {
    side = '<div class="verdict-side">' +
      '<p class="level">Most unusual month</p>' +
      `<p class="score"><span class="score-number">${formatScore(o.top.score)}</span><span class="score-label">${MONTHS[o.top.month]} ${o.top.year}</span></p>` +
      scoreMeter(o.top.score) + '<p class="score-help">0 is normal · 2 or more is rare</p>' +
      `<button type="button" class="link" id="seeTop">See ${MONTHS[o.top.month]} ${o.top.year}</button>` +
      '</div>'
  }
  el.innerHTML = where + `<h1 class="verdict-sentence">${o.sentence}</h1>` + side +
    '<dl class="facts">' +
      `<div><dt>${state.metric === 'frp' ? 'Fire energy' : 'Hotspots'}, ${state.fromYear} to ${state.toYear}</dt><dd>${formatValue(table.total, state.metric)}</dd></div>` +
      `<div><dt>Usual year</dt><dd>${formatValue(table.usualYear, state.metric)}</dd></div>` +
      (o.peakMonth !== undefined ? `<div><dt>Peak month</dt><dd>${MONTHS[o.peakMonth]}</dd></div>` : '') +
      `<div><dt>Record</dt><dd>${estimatedYears.length
        ? `${estimatedYears[0]} to ${estimatedYears[estimatedYears.length - 1]} estimated from MODIS`
        : (CONFIG.observedLabel || 'Real VIIRS')}</dd></div>` +
    '</dl>'
  if ($('seeTop')) $('seeTop').onclick = () => update({ month: monthKey(o.top.year, o.top.month) })
}

// the little strip under the score: where this month sits from "less" to "far more"
function scoreMeter(z) {
  const lo = -2.5, hi = 4.5
  const blocks = SCORE_STEPS.map(s => `<i style="background:${s.color}"></i>`).join('')
  if (z === null || Number.isNaN(z)) return `<div class="meter" aria-hidden="true">${blocks}</div>`
  const at = Math.max(0, Math.min(1, (z - lo) / (hi - lo))) * 100
  return `<div class="meter" aria-hidden="true">${blocks}<b style="left:${at.toFixed(1)}%"></b></div>`
}

// ---------------------------------------------------------------------------
// Fire regimes panel (Pearl's files)
// ---------------------------------------------------------------------------
function renderRegimes() {
  const body = $('regimeBody')
  if (!regimes) {
    body.innerHTML = '<p class="empty">No regime layer found. Run <code>python -m analysis.fire_regimes.run</code> to create <code>public/data/fire_regimes.geojson</code>.</p>'
    return
  }
  // count the cells of each regime inside the selected area
  const [south, west, north, east] = state.area
  const counts = new Map()
  let total = 0, shifted = 0
  for (const f of regimes.geojson.features) {
    const ring = f.geometry.type === 'Polygon' ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0]
    const lon = (ring[0][0] + ring[2][0]) / 2, lat = (ring[0][1] + ring[2][1]) / 2
    if (lat < south || lat > north || lon < west || lon > east) continue
    counts.set(f.properties.regime_id, (counts.get(f.properties.regime_id) || 0) + 1)
    total++
    if (f.properties.shifted) shifted++
  }
  if (!total) {
    body.innerHTML = '<p class="empty">No regime cells in this area.</p>'
    return
  }

  const summaries = new Map(((regimes.summary && regimes.summary.regimes) || []).map(r => [r.regime_id, r]))
  const names = new Map(regimes.geojson.features.map(f => [f.properties.regime_id, f.properties]))
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1])

  let html = '<ul class="regimes">'
  for (const [id, n] of sorted) {
    const s = summaries.get(id), p = names.get(id)
    const color = (s && s.color) || p.color || '#999'
    html += '<li>' +
      `<i class="regime-swatch" style="background:${color}"></i>` +
      `<div class="regime-text"><h3>${p.regime_name}</h3>${s && s.summary ? `<p>${s.summary}</p>` : ''}</div>` +
      `<div class="regime-side"><b>${Math.round(n / total * 100)}%</b><span>of cells</span>${s && s.monthly_curve ? monthShape(s.monthly_curve, '#B9C4BC') : ''}</div>` +
      '</li>'
  }
  html += '</ul>'

  let note = ''
  if (shifted) note += `${shifted} of ${total} cells here changed regime over the years. `
  const check = regimes.summary && regimes.summary.validation && regimes.summary.validation.kmeans
  if (check && check.silhouette !== undefined) {
    note += 'Regimes blend into each other at their edges, so treat the borders between them as approximate. '
  }
  note += 'Click any coloured square on the regime map for its story.'
  html += `<div class="panel-foot"><button type="button" class="button" id="regimeToggle">${state.layer === 'regimes' ? 'Show hotspots on the map' : 'Show regimes on the map'}</button>` +
    `<a class="button" href="${CONFIG.files.regimes}" download="fire_regimes.geojson">Download regime map (GeoJSON)</a>${note ? `<p>${note}</p>` : ''}</div>`
  body.innerHTML = html
  $('regimeToggle').onclick = () => update({ layer: state.layer === 'regimes' ? 'hotspots' : 'regimes' })
}

function regimeStory(p) {
  const s = regimes && regimes.summary && (regimes.summary.regimes || []).find(r => r.regime_id === p.regime_id)
  let html = `<div class="story"><p class="story-name"><i style="background:${p.color}"></i>${p.regime_name}</p>`
  if (s && s.summary) html += `<p>${s.summary}</p>`
  if (s && s.monthly_curve) html += `<div class="story-curve">${monthShape(s.monthly_curve, p.color)}</div>`
  if (p.hotspots_per_year !== undefined) html += `<p class="dim">This square: about ${formatNumber(p.hotspots_per_year)} hotspots a year${p.peak_month ? `, peak in ${MONTHS[p.peak_month - 1]}` : ''}.</p>`
  if (p.shifted) html += `<p class="story-shift">Changed over time: was "${p.shift_from}", now "${p.shift_to}".</p>`
  return html + '</div>'
}

// ---------------------------------------------------------------------------
// Forecast and validation panels (drawn once; they do not depend on the area yet)
// ---------------------------------------------------------------------------
function renderForecast(forecast) {
  // the panel only appears once a real forecast file exists (Snavya's model)
  const real = forecast && !forecast.is_demo && forecast.days && forecast.days.length
  $('forecastPanel').hidden = !real
  if (!real) return
  $('forecastSub').textContent = `${forecast.region || 'Whole data region'}, ${forecast.unit || 'hotspots per day'}.`
  $('forecastBody').innerHTML =
    '<div class="chart-key"><span><i class="key-usual is-solid"></i>Expected</span><span><i class="key-band"></i>Likely range</span><span><i class="key-dot"></i>Actual</span></div>' +
    '<div class="chart" id="forecastChart"></div>'
  drawForecastChart($('forecastChart'), forecast)
}

function renderValidation(validation) {
  const real = validation && !validation.is_demo && validation.metrics && validation.metrics.length
  $('validationPanel').hidden = !real
  if (!real) return
  $('validationSub').textContent = (validation.note || '') +
    (validation.overlap_period ? ` Overlap period: ${validation.overlap_period}.` : '')
  $('validationBody').innerHTML = '<ul class="checks">' + validation.metrics.map(m =>
    `<li><p class="check-value">${m.value}<span>${m.unit || ''}</span></p><p class="check-label">${m.label}</p>${m.detail ? `<p class="check-detail">${m.detail}</p>` : ''}</li>`).join('') + '</ul>'
}

// ---------------------------------------------------------------------------
// Buttons, menus and inputs
// ---------------------------------------------------------------------------
function flash(button, text) {
  const before = button.textContent
  button.textContent = text
  setTimeout(() => { button.textContent = before }, 1600)
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch (err) {
    return false
  }
}

function currentCsv() {
  const picked = focus()
  const first = picked ? monthIndex(state.month) : Math.max(0, monthIndex(`${state.fromYear}-01`))
  const lastKey = `${state.toYear}-12`
  const last = picked ? first : (monthIndex(lastKey) >= 0 ? monthIndex(lastKey) : data.months.length - 1)
  const name = `hotspots_${state.area.join('_')}_${picked ? state.month : `${state.fromYear}-${state.toYear}`}.csv`
  return { ...rowsAsCsv(state.area, first, last), name }
}

function wireSearch(input, results, onPick) {
  let hits = []
  const close = () => { results.hidden = true; input.setAttribute('aria-expanded', 'false') }
  const pick = place => { input.value = ''; close(); onPick(place) }
  input.oninput = () => {
    const q = input.value.trim().toLowerCase()
    if (q.length < 2) { close(); return }
    const seen = new Set()
    hits = mapView.getPlaces()
      .filter(p => p.name && p.name.toLowerCase().includes(q) && !seen.has(p.name) && seen.add(p.name))
      .sort((a, b) => (a.name.toLowerCase().startsWith(q) ? 0 : 1) - (b.name.toLowerCase().startsWith(q) ? 0 : 1) || a.rank - b.rank)
      .slice(0, 8)
    results.innerHTML = hits.length
      ? hits.map((p, i) => `<li role="option" data-i="${i}">${p.name}<span>${degrees(Math.round(p.lat * 10) / 10, 'N', 'S')}, ${degrees(Math.round(p.lon * 10) / 10, 'E', 'W')}</span></li>`).join('')
      : '<li class="none">No place with that name yet. Try a nearby city.</li>'
    results.hidden = false
    input.setAttribute('aria-expanded', 'true')
  }
  input.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); if (hits[0]) pick(hits[0]) }
    if (e.key === 'Escape') close()
  }
  results.onmousedown = e => {
    const li = e.target.closest('li[data-i]')
    if (li) { e.preventDefault(); pick(hits[Number(li.dataset.i)]) }
  }
  input.onblur = () => setTimeout(close, 150)
}

// the search screen and the app
// ---------------------------------------------------------------------------
// Pages (sidebar): Home and Fire Map; features open as a window over the map
// ---------------------------------------------------------------------------
const FEATURES = {
  trends: 'Trends',
  anomalies: 'Anomalies',
  patterns: 'Patterns',
  listen: 'Listen to the data',
  about: 'About the data',
}

function markSidebar() {
  document.querySelectorAll('.side-item').forEach(b => {
    const on = feature ? b.dataset.feature === feature : b.dataset.page === page
    b.setAttribute('aria-current', on ? 'true' : 'false')
  })
}

function setPage(next) {
  const was = page
  page = next
  document.body.classList.toggle('page-home', page === 'home')
  document.body.classList.toggle('page-map', page === 'map')
  if (page === 'home') closeFeature()
  markSidebar()
  writeAddress()
  window.scrollTo(0, 0)
  if (page === 'map' && was !== 'map') {
    // wait for the layout to settle before sizing the map and zooming
    requestAnimationFrame(() => setTimeout(() => {
      mapView.refresh()
      if (state.hasArea) { mapView.fitArea(); showTip(false) } else mapView.showWorld()
    }, 60))
  }
  if (page === 'home') $('heroSearch').focus()
}

function openFeature(name) {
  feature = name
  // Patterns are the fire regimes, so the map switches to that layer (and back for the others)
  if (name === 'patterns' && regimes && state.layer !== 'regimes') update({ layer: 'regimes' })
  else if ((name === 'trends' || name === 'anomalies') && state.layer !== 'hotspots') update({ layer: 'hotspots' })
  if (page !== 'map') setPage('map')
  renderFeatureWindow()
  markSidebar()
  $('featureBody').scrollTop = 0
}

function closeFeature() {
  if (feature === 'listen') listening?.stop()
  feature = null
  renderFeatureWindow()
  markSidebar()
}

function renderFeatureWindow() {
  const win = $('featureWindow')
  win.hidden = !feature
  if (!feature) return
  $('featureTitle').textContent = FEATURES[feature]
  const needsArea = feature !== 'about'
  const empty = needsArea && !state.hasArea
  $('featureEmpty').hidden = !empty
  $('featureWhere').textContent = empty || !needsArea ? '' : `${areaName() === 'Custom area' ? areaText() : areaName()} · ${state.fromYear} to ${state.toYear}`
  document.querySelectorAll('.fw-group').forEach(g => g.classList.toggle('is-open', !empty && g.dataset.group === feature))
}

// the most unusual months of the selected area, for the Anomalies window
function renderAnomalies() {
  const items = []
  table.years.forEach((year, row) => {
    for (let m = 0; m < 12; m++) {
      const z = table.score[row][m], v = table.value[row][m]
      if (Number.isFinite(z) && z >= 2 && v > 0) items.push({ year, m, z, v, usual: table.usual[m].median })
    }
  })
  items.sort((a, b) => b.z - a.z)
  const unit = state.metric === 'frp' ? 'fire energy' : 'hotspots'
  const fmt = v => state.metric === 'frp' ? formatPower(v) : formatNumber(v)
  $('anomalyList').innerHTML = items.length
    ? items.slice(0, 8).map(a => `<li><button type="button" data-month="${monthKey(a.year, a.m)}" aria-pressed="${state.month === monthKey(a.year, a.m)}">
        <span class="an-when">${MONTHS[a.m]} ${a.year}</span>
        <span class="an-score">+${a.z.toFixed(1)}σ</span>
        <span class="an-detail">${fmt(a.v)} ${unit}; a usual ${MONTHS[a.m]} has ${fmt(a.usual)}</span>
      </button></li>`).join('')
    : `<li class="an-none">No month here was 2σ or more above usual between ${state.fromYear} and ${state.toYear}.</li>`
  $('anomalyList').querySelectorAll('button').forEach(b => {
    b.onclick = () => update({ month: state.month === b.dataset.month ? null : b.dataset.month })
  })
}

function showApp() { setPage('map') }
// a short tip on the map that fades away (shown once, or again from the menu)
let tipTimer = null
function showTip(force) {
  let seen = false
  try { seen = localStorage.getItem('emberTipSeen') === '1' } catch (err) { /* storage may be blocked */ }
  if (seen && !force) return
  const tip = $('mapHint')
  tip.hidden = false
  requestAnimationFrame(() => tip.classList.add('is-shown'))
  clearTimeout(tipTimer)
  tipTimer = setTimeout(() => { tip.classList.remove('is-shown'); setTimeout(() => { tip.hidden = true }, 400) }, 6500)
  try { localStorage.setItem('emberTipSeen', '1') } catch (err) { /* fine */ }
}

function showStart() { setPage('home') }

function stopListening() {
  stopCalendar()
  markPlaying($('calendar'), null)
  playingAt = null
  $('mapPlay').setAttribute('aria-pressed', 'false')
  drawMapPeriod(focus())
}

function startPlaying() {
  listening?.stop()
  $('mapPlay').setAttribute('aria-pressed', 'true')
  playCalendar(table, (row, month) => {
    markPlaying($('calendar'), row, month)
    if (row === null || row === undefined) return
    playingAt = { year: table.years[row], month: Number.isInteger(month) ? month : 0 }
    drawMapPeriod(focus())
  }, stopListening)
}

function wireControls() {
  // example areas as chips (on the search screen)
  const chips = $('presetChips')
  chips.innerHTML = [...CONFIG.presets.map(p => p.name), 'Whole world']
    .map(n => `<button type="button" class="chip" aria-pressed="false">${n}</button>`).join('')
  chips.querySelectorAll('button').forEach(b => {
    b.onclick = () => {
      const preset = CONFIG.presets.find(p => p.name === b.textContent)
      update({ area: [...(preset ? preset.box : data.bounds)] })
      showApp(); mapView.fitArea()
    }
  })

  // place search: the place names the map already has, no outside service
  const goTo = place => {
    const [s0, w0, n0, e0] = state.area
    const hLat = Math.min(4, Math.max(1.5, (n0 - s0) / 2)), hLon = Math.min(6, Math.max(2, (e0 - w0) / 2))
    update({ area: [place.lat - hLat, place.lon - hLon, place.lat + hLat, place.lon + hLon].map(v => Math.round(v * 10) / 10) })
    showApp(); mapView.fitArea()
  }
  wireSearch($('heroSearch'), $('heroResults'), goTo)
  wireSearch($('placeSearch'), $('placeResults'), goTo)

  // Layers popover on the map (what to show, map style, key)
  const layersPanel = $('layersPanel'), layersButton = $('layersButton')
  layersButton.onclick = () => {
    layersPanel.hidden = !layersPanel.hidden
    layersButton.setAttribute('aria-expanded', String(!layersPanel.hidden))
  }
  document.addEventListener('click', e => {
    if (!layersPanel.hidden && !e.target.closest('.map-tools')) { layersPanel.hidden = true; layersButton.setAttribute('aria-expanded', 'false') }
  })

  // time slider on the map: 0 = the selected years, then one step per year
  const slider = $('mapYear')
  slider.max = data.lastYear - data.firstYear + 1
  slider.oninput = () => {
    const v = Number(slider.value)
    update({ mapYear: v ? data.firstYear + v - 1 : null, month: null })
  }
  $('mapPlay').onclick = () => {
    if (isPlaying()) { stopListening(); return }
    startPlaying()
  }

  // typed coordinates
  $('areaForm').onsubmit = event => {
    event.preventDefault()
    const s = Number($('south').value), w = Number($('west').value), n = Number($('north').value), e = Number($('east').value)
    const error = $('areaError')
    let problem = ''
    if (![s, w, n, e].every(Number.isFinite) || [$('south'), $('west'), $('north'), $('east')].some(i => i.value.trim() === '')) problem = 'Enter a number in all four boxes. West of Greenwich and south of the equator are negative, for example West = -119.'
    else if (n <= s) problem = 'North must be a bigger number than South.'
    else if (e <= w) problem = 'East must be a bigger number than West. For western Canada both are negative, for example West = -119 and East = -110.'
    else if (s < -90 || n > 90 || w < -180 || e > 180) problem = 'Latitude goes from -90 to 90 and longitude from -180 to 180.'
    error.hidden = !problem
    error.textContent = problem
    if (problem) return
    update({ area: [s, w, n, e] }); mapView.fitArea()
    mapView.fitArea()
    $('areaForm').hidden = true
  }

  // years
  let options = ''
  for (let y = data.firstYear; y <= data.lastYear; y++) options += `<option>${y}</option>`
  $('fromYear').innerHTML = options; $('toYear').innerHTML = options
  $('fromYear').onchange = () => {
    const from = Number($('fromYear').value)
    update({ fromYear: from, toYear: Math.max(from, state.toYear) })
  }
  $('toYear').onchange = () => {
    const to = Number($('toYear').value)
    update({ toYear: to, fromYear: Math.min(to, state.fromYear) })
  }

  // the two- and three-way switches
  document.querySelectorAll('[data-metric]').forEach(b => { b.onclick = () => update({ metric: b.dataset.metric }) })
  document.querySelectorAll('[data-mode]').forEach(b => { b.onclick = () => update({ mode: b.dataset.mode }) })
  document.querySelectorAll('[data-layer]').forEach(b => { b.onclick = () => update({ layer: b.dataset.layer }) })

  // The listening feature shares month selection with the calendar and map.
  listening = createListeningPanel({
    onPick: (year, month) => { if (isPlaying()) stopListening(); update({ month: monthKey(year,month) }, true) },
    onMode: mode => update({mode}),
    onHighlight: (row, month) => markPlaying($('calendar'),row,month),
  })
  // the calendar's Listen button now opens the Listen window
  $('listenButton').onclick = () => openFeature('listen')
  $('listenButton').removeAttribute('aria-controls'); $('listenButton').removeAttribute('aria-expanded')

  // the ⋮ menu
  const menu = $('appMenu'), menuButton = $('menuButton')
  const panels = [$('exportPanel'), $('areaForm')]
  const closeMenu = () => { menu.hidden = true; menuButton.setAttribute('aria-expanded', 'false') }
  const closePanels = () => { panels.forEach(p => { p.hidden = true }); $('exportButton').setAttribute('aria-expanded', 'false'); $('coordsButton').setAttribute('aria-expanded', 'false') }
  const openPanel = (panel, button) => { closeMenu(); closePanels(); panel.hidden = false; button.setAttribute('aria-expanded', 'true') }
  menuButton.onclick = () => {
    const open = menu.hidden
    closePanels()
    menu.hidden = !open
    menuButton.setAttribute('aria-expanded', String(open))
    if (open) menu.querySelector('button').focus()
  }
  document.addEventListener('click', event => {
    if (!event.target.closest('.kebab')) { closeMenu(); closePanels() }
  })
  document.addEventListener('keydown', event => { if (event.key === 'Escape') { closeMenu(); closePanels() } })

  $('shareButton').onclick = async () => {
    const ok = await copyText(location.href)
    flash($('shareButton'), ok ? 'Link copied' : 'Copy the address bar instead')
  }
  $('exportButton').onclick = () => openPanel($('exportPanel'), $('exportButton'))
  $('coordsButton').onclick = () => { openPanel($('areaForm'), $('coordsButton')); $('north').focus() }
  $('aboutButton').onclick = () => { closeMenu(); openFeature('about') }

  // sidebar and home cards
  document.querySelectorAll('[data-page]').forEach(b => { b.onclick = () => { if (b.dataset.page === 'map') closeFeature(); setPage(b.dataset.page) } })
  document.querySelectorAll('[data-feature]').forEach(b => { b.onclick = () => (feature === b.dataset.feature ? closeFeature() : openFeature(b.dataset.feature)) })
  $('featureClose').onclick = closeFeature
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && feature && !event.target.closest('input, select')) closeFeature() })
  $('homeButton').onclick = () => { closeMenu(); showStart() }
  $('tipButton').onclick = () => { closeMenu(); showApp(); showTip(true) }
  $('downloadButton').onclick = () => {
    const csv = currentCsv()
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([csv.text], { type: 'text/csv' }))
    link.download = csv.name
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(link.href), 5000)
  }
  $('copyCsvButton').onclick = async () => {
    const ok = await copyText(currentCsv().text)
    flash($('copyCsvButton'), ok ? 'Copied' : 'Could not copy')
  }
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
async function start() {
  // a shared link (with an area in it) opens straight on the map; otherwise start on the search screen
  const firstParams = new URLSearchParams(location.hash.slice(1))
  page = firstParams.get('area') || firstParams.get('page') === 'map' ? 'map' : 'home'
  document.body.classList.toggle('page-home', page === 'home')
  document.body.classList.toggle('page-map', page === 'map')

  mapView = createMap($('map'), $('mapLegend'), {
    onAreaChange: area => update({ area }),
    onRegimeClick: (props, latlng) => mapView.openPopup(regimeStory(props), latlng),
  })
  mapView.loadBasemap()   // not awaited: the map fills in while the data loads

  try {
    await loadHotspots()
  } catch (err) {
    $('verdict').innerHTML = '<h1 class="verdict-sentence">The fire record did not load.</h1>' +
      `<p class="verdict-help">${err.message}. If you opened index.html by double-clicking it, start a local server instead (see README).</p>`
    return
  }

  state.fromYear = data.firstYear
  state.toYear = data.lastYear
  $('heroStatus').textContent = `NASA satellite fire detections for the whole world, ${data.firstYear} to ${data.lastYear}.`
  readAddress()
  wireControls()
  mapView.buildCells()

  // optional files: each panel explains what to add if its file is missing
  const [geojson, summary, forecast, validation] = await Promise.all([
    loadJson(CONFIG.files.regimes), loadJson(CONFIG.files.regimeSummary),
    loadJson(CONFIG.files.forecast), loadJson(CONFIG.files.validation),
  ])
  if (geojson && geojson.features && geojson.features.length) {
    regimes = { geojson, summary }
    mapView.setRegimes(geojson)
  } else {
    state.layer = 'hotspots'
    document.querySelector('[data-layer="regimes"]').disabled = true
  }

  mapView.setArea(state.area, { fit: state.hasArea })
  if (!state.hasArea) mapView.showWorld()
  render()
  markSidebar()
  renderForecast(forecast)
  renderValidation(validation)

  window.addEventListener('hashchange', () => { listening?.stop(); readAddress(); render() })
}

start()
