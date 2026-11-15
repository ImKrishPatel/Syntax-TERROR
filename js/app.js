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
import { playCalendar, stopCalendar, isPlaying } from './sound.js'

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
}

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
  if (area.length === 4 && area.every(v => Number.isFinite(v)) && area[0] < area[2] && area[1] < area[3]) state.area = area
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

function update(changes) {
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

  // --- map ---
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
    // bring the sentence about this month back into view if it has scrolled away
    if ($('verdict').getBoundingClientRect().top < 60) {
      const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      $('verdict').scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' })
    }
  })

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
  if (!forecast || !forecast.days || !forecast.days.length) {
    $('forecastSub').textContent = ''
    $('forecastBody').innerHTML = '<p class="empty">No forecast yet. Add <code>public/data/forecast.json</code> (format in DATA_FORMAT.md) and it will show up here.</p>'
    return
  }
  $('forecastSub').textContent = `${forecast.region || 'Whole data region'}, ${forecast.unit || 'hotspots per day'}.` +
    (forecast.is_demo ? ' Sample numbers until the forecast model is connected.' : '')
  $('forecastBody').innerHTML =
    '<div class="chart-key"><span><i class="key-usual is-solid"></i>Expected</span><span><i class="key-band"></i>Likely range</span><span><i class="key-dot"></i>Actual</span></div>' +
    '<div class="chart" id="forecastChart"></div>'
  drawForecastChart($('forecastChart'), forecast)
}

function renderValidation(validation) {
  if (!validation || !validation.metrics || !validation.metrics.length) {
    $('validationSub').textContent = ''
    $('validationBody').innerHTML = '<p class="empty">No validation numbers yet. Add <code>public/data/validation.json</code> (format in DATA_FORMAT.md).</p>'
    return
  }
  $('validationSub').textContent = (validation.note || '') +
    (validation.overlap_period ? ` Overlap period: ${validation.overlap_period}.` : '') +
    (validation.is_demo ? ' Sample numbers until the real check is run.' : '')
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

function stopListening() {
  stopCalendar()
  markPlaying($('calendar'), null)
  playingAt = null
  $('listenButton').setAttribute('aria-pressed', 'false')
  $('listenText').textContent = 'Listen'
  $('mapPlay').setAttribute('aria-pressed', 'false')
  drawMapPeriod(focus())
}

function startPlaying() {
  $('listenButton').setAttribute('aria-pressed', 'true')
  $('mapPlay').setAttribute('aria-pressed', 'true')
  $('listenText').textContent = 'Stop'
  playCalendar(table, (row, month) => {
    markPlaying($('calendar'), row, month)
    if (row === null || row === undefined) return
    playingAt = { year: table.years[row], month: Number.isInteger(month) ? month : 0 }
    $('listenText').textContent = `Stop (${table.years[row]})`
    drawMapPeriod(focus())
  }, stopListening)
}

function wireControls() {
  // example areas as chips
  const chips = $('presetChips')
  chips.innerHTML = [...CONFIG.presets.map(p => p.name), 'Whole world']
    .map(n => `<button type="button" class="chip" aria-pressed="false">${n}</button>`).join('')
  chips.querySelectorAll('button').forEach(b => {
    b.onclick = () => {
      const preset = CONFIG.presets.find(p => p.name === b.textContent)
      update({ area: [...(preset ? preset.box : data.bounds)] })
      mapView.fitArea()
    }
  })

  // place search: the place names the map already has, no outside service
  const search = $('placeSearch'), results = $('placeResults')
  let hits = []
  const closeResults = () => { results.hidden = true; search.setAttribute('aria-expanded', 'false') }
  const goTo = place => {
    const [s0, w0, n0, e0] = state.area
    const hLat = Math.min(4, (n0 - s0) / 2), hLon = Math.min(6, (e0 - w0) / 2)
    update({ area: [place.lat - hLat, place.lon - hLon, place.lat + hLat, place.lon + hLon].map(v => Math.round(v * 10) / 10) })
    mapView.fitArea()
    search.value = place.name
    closeResults()
  }
  search.oninput = () => {
    const q = search.value.trim().toLowerCase()
    if (q.length < 2) { closeResults(); return }
    const seen = new Set()
    hits = mapView.getPlaces()
      .filter(p => p.name && p.name.toLowerCase().includes(q) && !seen.has(p.name) && seen.add(p.name))
      .sort((a, b) => (a.name.toLowerCase().startsWith(q) ? 0 : 1) - (b.name.toLowerCase().startsWith(q) ? 0 : 1) || a.rank - b.rank)
      .slice(0, 8)
    results.innerHTML = hits.length
      ? hits.map((p, i) => `<li role="option" data-i="${i}">${p.name}<span>${degrees(Math.round(p.lat * 10) / 10, 'N', 'S')}, ${degrees(Math.round(p.lon * 10) / 10, 'E', 'W')}</span></li>`).join('')
      : '<li class="none">No place with that name. Try a nearby city.</li>'
    results.hidden = false
    search.setAttribute('aria-expanded', 'true')
  }
  search.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); if (hits[0]) goTo(hits[0]) }
    if (e.key === 'Escape') closeResults()
  }
  results.onmousedown = e => {
    const li = e.target.closest('li[data-i]')
    if (li) { e.preventDefault(); goTo(hits[Number(li.dataset.i)]) }
  }
  search.onblur = () => setTimeout(closeResults, 150)

  // map key behind an info button
  $('legendToggle').onclick = () => {
    const legend = $('mapLegend')
    legend.hidden = !legend.hidden
    $('legendToggle').setAttribute('aria-expanded', String(!legend.hidden))
  }

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
    update({ area: [s, w, n, e] })
    mapView.fitArea()
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
  document.querySelectorAll('[data-basemap]').forEach(b => {
    b.onclick = () => {
      mapView.setBasemap(b.dataset.basemap)
      document.querySelectorAll('[data-basemap]').forEach(x => x.setAttribute('aria-pressed', String(x === b)))
    }
  })

  // listen (the calendar button and the map's play button share one player; the map follows the sound)
  $('listenButton').onclick = () => {
    if (isPlaying()) { stopListening(); return }
    startPlaying()
  }

  // share
  $('shareButton').onclick = async () => {
    const ok = await copyText(location.href)
    flash($('shareButton'), ok ? 'Link copied' : 'Copy the address bar instead')
  }

  // export
  const panel = $('exportPanel'), exportButton = $('exportButton')
  const closeExport = () => { panel.hidden = true; exportButton.setAttribute('aria-expanded', 'false') }
  exportButton.onclick = () => {
    panel.hidden = !panel.hidden
    exportButton.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true')
  }
  document.addEventListener('click', event => {
    if (!panel.hidden && !event.target.closest('.export')) closeExport()
  })
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeExport() })
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
  $('demoTag').hidden = !CONFIG.isDemoData

  mapView = createMap($('map'), $('mapLegend'), {
    onAreaChange: area => update({ area }),
    onRegimeClick: (props, latlng) => mapView.openPopup(regimeStory(props), latlng),
  })
  mapView.loadBasemap()   // not awaited: the map fills in while the data loads
  mapView.setBasemap('satellite')

  try {
    await loadHotspots()
  } catch (err) {
    $('verdict').innerHTML = '<h1 class="verdict-sentence">The fire record did not load.</h1>' +
      `<p class="verdict-help">${err.message}. If you opened index.html by double-clicking it, start a local server instead (see README).</p>`
    return
  }

  state.fromYear = data.firstYear
  state.toYear = data.lastYear
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

  mapView.setArea(state.area, { fit: true })
  render()
  renderForecast(forecast)
  renderValidation(validation)

  window.addEventListener('hashchange', () => { readAddress(); render() })
}

start()
