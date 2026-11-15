// Ties everything together.
//
// The idea is simple: there is ONE object called `state` that says what the
// user has selected (area, years, month, ...). Whenever it changes we call
// render(), which redraws every part of the page from the state. The state is
// also written into the page address, so a link opens the exact same view.
//
// Two more things are kept apart from the state:
//   - which page and panel are open (`page` and `feature`)
//   - settings the browser remembers (`settings`: dark mode, favourites, ...)

import { CONFIG, MONTHS } from './config.js'
import { data, loadHotspots, loadJson, monthlyTotals, cellTotals, rowsAsCsv, monthIndex, cellsInArea } from './data.js'
import { buildTable, monthVerdict, overviewVerdict, formatValue, formatNumber, formatPower, formatScore, LEVEL_TEXT } from './stats.js'
import { SCORE_STEPS, setPalette, regimeColor } from './colors.js'
import { createMap } from './map.js?v=voice-place-detail-15'
import { drawCalendar, markPlaying } from './calendar.js'
import { drawSeasonChart, drawForecastChart, monthShape } from './charts.js'
import { createListeningPanel } from './sound.js?v=voice-place-detail-15'
import { playCalendar, stopCalendar, isPlaying } from './sweep.js'
import { voicePlaceBounds } from './place-bounds.js'
import { wireSearch, resolveSpokenPlace } from './search.js?v=voice-place-detail-15'
import { createVoiceNavigator } from './voice-navigation.js?v=voice-place-detail-15'
import { startFlames } from './flames.js'
import { loadPref, savePref, saveTheme } from './prefs.js?v=voice-place-detail-15'
import { initChat } from './chat.js'

const $ = id => document.getElementById(id)

const state = {
  area: [...CONFIG.defaultArea],   // [south, west, north, east]
  label: null,                     // the place name, when the area came from a search or a favourite
  hasArea: false,                  // false until a place is chosen: the map shows the whole world
  fromYear: 0,
  toYear: 0,
  month: null,                     // '2023-05' when one month is selected, otherwise null
  metric: 'count',                 // 'count' (hotspots) or 'frp' (fire energy)
  mode: 'amount',                  // calendar colours: 'amount' or 'score'
  layer: 'hotspots',               // map layer: 'hotspots' or 'regimes'
  mapYear: null,                   // one year shown on the map (time slider), or null for the selected years
}
let page = 'home'                  // 'home' or 'map'
let feature = null                 // the open panel: 'trends', 'anomalies', 'patterns', 'forecast', 'listen', 'about'

const settings = {
  dark: document.documentElement.dataset.theme === 'dark',
  safeColors: loadPref('safeColors', false),      // colours for colour-blind viewers
  speech: loadPref('speech', false),              // spoken month descriptions in the Listen panel
  sideClosed: loadPref('sideClosed', window.innerWidth < 760),
  basemap: loadPref('basemap', 'map'),            // 'map', 'satellite', 'terrain' or 'nasa'
  favourites: loadPref('favourites', []),         // [{ name, box }]
}

const PANELS = {
  trends: 'Trends',
  anomalies: 'Anomalies',
  patterns: 'Patterns',
  forecast: 'Forecast',
  listen: 'Listen to the data',
  about: 'About the data',
}
const NEEDS_AREA = ['trends', 'anomalies', 'patterns', 'listen']

let ready = false       // true once hotspots.csv has loaded
let mapView = null
let flames = null
let listening = null
let table = null        // the calendar numbers for the current area and years
let regimes = null      // { geojson, summary } once loaded
let forecastData = null
let validationData = null
let playingAt = null    // { year, month } while the map is playing through the record

// ---------------------------------------------------------------------------
// State <-> page address
// ---------------------------------------------------------------------------
function readAddress() {
  const params = new URLSearchParams(location.hash.slice(1))
  const area = (params.get('area') || '').split(',').map(Number)
  state.hasArea = area.length === 4 && area.every(v => Number.isFinite(v)) && area[0] < area[2] && area[1] < area[3]
  if (state.hasArea) state.area = area
  state.label = state.hasArea ? params.get('name') : null
  const month = params.get('month')
  state.month = month && /^\d{4}-\d{2}$/.test(month) ? month : null
  state.metric = params.get('measure') === 'frp' ? 'frp' : 'count'
  state.mode = params.get('colour') === 'score' ? 'score' : 'amount'
  state.layer = params.get('layer') === 'regimes' ? 'regimes' : 'hotspots'
  const tab = params.get('tab')
  feature = PANELS[tab] ? tab : null
  page = state.hasArea || feature || params.get('page') === 'map' ? 'map' : 'home'
}

// the years need the data file (to know which years exist), so they are read after it loads
function readYears() {
  const years = (new URLSearchParams(location.hash.slice(1)).get('years') || '').split('-').map(Number)
  if (years.length === 2 && years[0] >= data.firstYear && years[1] <= data.lastYear && years[0] <= years[1]) {
    state.fromYear = years[0]; state.toYear = years[1]
  } else {
    state.fromYear = data.firstYear; state.toYear = data.lastYear
  }
  if (state.month && monthIndex(state.month) < 0) state.month = null
}

function writeAddress() {
  if (!ready) return
  const params = new URLSearchParams()
  if (state.hasArea) {
    params.set('area', state.area.join(','))
    if (state.label) params.set('name', state.label)
    params.set('years', `${state.fromYear}-${state.toYear}`)
    if (state.month) params.set('month', state.month)
    if (state.metric === 'frp') params.set('measure', 'frp')
    if (state.mode === 'score') params.set('colour', 'score')
    if (state.layer === 'regimes') params.set('layer', 'regimes')
  } else if (page === 'map') {
    params.set('page', 'map')
  }
  if (feature) params.set('tab', feature)
  const text = params.toString().replace(/%2C/g, ',')
  try {
    history.replaceState(null, '', text ? '#' + text : location.pathname + location.search)
  } catch (err) { /* some embedded viewers do not allow this; the app still works */ }
}

// Change the state, then redraw. Every button and control ends up here.
function update(changes, fromAudio = false) {
  if (!fromAudio) listening?.stop()
  if (changes.area) {
    state.hasArea = true
    if (!('label' in changes)) state.label = null   // the box was moved by hand, so the old name no longer fits
  }
  Object.assign(state, changes)
  // a selected month must be inside the selected years
  if (state.month && ready) {
    const y = Number(state.month.slice(0, 4))
    if (y < state.fromYear || y > state.toYear) state.month = null
  }
  writeAddress()
  render()
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const sameBox = (a, b) => a.every((v, i) => v === b[i])

function areaName() {
  if (state.label) return state.label
  const saved = [...settings.favourites, ...CONFIG.presets].find(p => sameBox(p.box, state.area))
  return saved ? saved.name : 'Custom area'
}
function degrees(v, positive, negative) {
  return `${Math.abs(v)}°${v < 0 ? negative : positive}`
}
function areaText() {
  const [s, w, n, e] = state.area
  return `${degrees(s, 'N', 'S')} to ${degrees(n, 'N', 'S')}, ${degrees(w, 'E', 'W')} to ${degrees(e, 'E', 'W')}`
}
// the place name if there is one, otherwise the edges of the box
function areaTitle() {
  return areaName() === 'Custom area' ? areaText() : areaName()
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
  // these parts do not need the data file
  setPressed('[data-metric]', 'metric', state.metric)
  setPressed('[data-mode]', 'mode', state.mode)
  setPressed('[data-layer]', 'layer', state.layer)
  document.body.classList.toggle('is-plain', !state.hasArea)
  mapView.setPlain(!state.hasArea)
  if (state.hasArea) mapView.setArea(state.area)
  $('areaPill').hidden = !state.hasArea
  $('areaPillText').textContent = areaTitle()
  drawStar()
  renderPanel()
  if (!ready) return

  const picked = focus()
  const totals = monthlyTotals(state.area)
  table = buildTable(totals, state.metric, state.fromYear, state.toYear)
  const cellsInside = cellsInArea(state.area).reduce((a, b) => a + b, 0)

  $('fromYear').value = state.fromYear; $('toYear').value = state.toYear

  // --- map ---
  drawMapPeriod(picked)
  mapView.showLayer(state.layer)

  // --- summary sentence ---
  renderVerdict(picked, totals, cellsInside)

  // --- calendar ---
  drawCalendar($('calendar'), $('calendarLegend'), table, state.mode, picked, (year, month) => {
    const key = monthKey(year, month)
    update({ month: state.month === key ? null : key })   // selecting the same square again clears it
  })
  renderAnomalies()

  listening?.update(table, picked, JSON.stringify([state.area, state.fromYear, state.toYear, state.metric]), cellsInside > 0, state.mode)

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

  // The burn view shows a satellite picture from the same time: the shown month, or
  // with no month chosen, the area's biggest fire month (of the shown year, if there is one).
  let showYear = year, showMonth = playingAt ? playingAt.month : picked ? picked.month : null
  if (showMonth === null && table) {
    let best = -1
    table.years.forEach((y, row) => {
      if (year && y !== year) return
      for (let m = 0; m < 12; m++) {
        const v = table.value[row][m]
        if (v > best) { best = v; showYear = y; showMonth = m }
      }
    })
  }
  if (showMonth === null) { showYear = year || state.toYear; showMonth = 7 }
  // while Play runs the picture stays still: reloading it every month would flicker
  if (!playingAt) mapView.setImageryDate(`${showYear}-${String(showMonth + 1).padStart(2, '0')}-15`)
  $('imageryDate').textContent = mapView.imageryPeriod()
}

// "Real satellite observations" or "Partly estimated" next to the place name
function confidenceBadge() {
  const est = table.years.filter((_, i) => table.yearEstimated[i] >= 0.5)
  if (est.length) {
    return `<span class="badge is-estimated" title="Before 2012 only the older MODIS sensor existed, so those years are converted by our model.">Partly estimated: ${est[0]} to ${est[est.length - 1]}</span>`
  }
  return `<span class="badge is-observed" title="Every number here comes straight from NASA satellite fire detections (${CONFIG.observedLabel || 'VIIRS'}).">Real satellite observations</span>`
}

function renderVerdict(picked, totals, cellsInside) {
  const el = $('verdict')
  const where = `<p class="verdict-where"><span>${areaText()}</span>${cellsInside ? confidenceBadge() : ''}</p>`

  if (cellsInside === 0) {
    el.className = 'verdict level-none'
    el.innerHTML = where +
      '<h3 class="verdict-sentence">There is no data for this area.</h3>' +
      '<p class="verdict-help">No fire-data grid centres fall inside this selection. A close city view can be smaller than our 2-degree data cells. Enlarge the box to inspect regional data; missing coverage does not mean no fires.</p>'
    return
  }

  if (picked && !Number.isFinite(table.value[picked.year - table.years[0]]?.[picked.month])) {
    el.className = 'verdict level-none'
    el.innerHTML = where + `<h3 class="verdict-sentence">No data for ${MONTHS[picked.month]} ${picked.year}.</h3>` +
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
      `<h3 class="verdict-sentence">${v.sentence}</h3>` +
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
  el.innerHTML = where + `<h3 class="verdict-sentence">${o.sentence}</h3>` + side +
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

// the most unusual months of the selected area, for the Anomalies panel
function renderAnomalies() {
  const items = []
  table.years.forEach((year, row) => {
    for (let m = 0; m < 12; m++) {
      const z = table.score[row][m], v = table.value[row][m]
      if (Number.isFinite(z) && z >= CONFIG.unusualSigma && v > 0) items.push({ year, m, z, v, usual: table.usual[m].median })
    }
  })
  items.sort((a, b) => b.z - a.z)
  const unit = state.metric === 'frp' ? 'fire energy' : 'hotspots'
  const fmt = v => state.metric === 'frp' ? formatPower(v) : formatNumber(v)
  $('anomalyList').innerHTML = items.length
    ? items.slice(0, 10).map(a => `<li><button type="button" data-month="${monthKey(a.year, a.m)}" aria-pressed="${state.month === monthKey(a.year, a.m)}">
        <span class="an-when">${MONTHS[a.m]} ${a.year}</span>
        <span class="an-score">+${a.z.toFixed(1)}σ</span>
        <span class="an-detail">${fmt(a.v)} ${unit}; a usual ${MONTHS[a.m]} has ${fmt(a.usual)}</span>
      </button></li>`).join('')
    : `<li class="an-none">No month here was ${CONFIG.unusualSigma}σ or more above usual between ${state.fromYear} and ${state.toYear}.</li>`
  $('anomalyList').querySelectorAll('button').forEach(b => {
    b.onclick = () => update({ month: state.month === b.dataset.month ? null : b.dataset.month })
  })
}

// ---------------------------------------------------------------------------
// Patterns panel: the fire regimes inside the selected area
// ---------------------------------------------------------------------------
// counts the cells of each regime inside the selected area
function regimesInArea() {
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
  return {
    total, shifted,
    sorted: [...counts.entries()].sort((a, b) => b[1] - a[1]),
    summaries: new Map(((regimes.summary && regimes.summary.regimes) || []).map(r => [r.regime_id, r])),
    names: new Map(regimes.geojson.features.map(f => [f.properties.regime_id, f.properties])),
  }
}

function renderRegimes() {
  const body = $('regimeBody')
  if (!regimes) {
    body.innerHTML = '<p class="empty">No regime layer found. Run <code>python -m analysis.fire_regimes.run</code> to create <code>public/data/fire_regimes.geojson</code>.</p>'
    return
  }
  const r = regimesInArea()
  if (!r.total) {
    body.innerHTML = '<p class="empty">No regime cells in this area.</p>'
    return
  }

  let html = '<ul class="regimes">'
  for (const [id, n] of r.sorted) {
    const s = r.summaries.get(id), p = r.names.get(id)
    html += '<li>' +
      `<i class="regime-swatch" style="background:${regimeColor(id, (s && s.color) || p.color)}"></i>` +
      `<div class="regime-text"><h4>${p.regime_name}</h4>${s && s.summary ? `<p>${s.summary}</p>` : ''}</div>` +
      `<div class="regime-side"><b>${Math.round(n / r.total * 100)}%</b><span>of cells</span>${s && s.monthly_curve ? monthShape(s.monthly_curve, '#9AA8A0') : ''}</div>` +
      '</li>'
  }
  html += '</ul>'

  let note = ''
  if (r.shifted) note += `${r.shifted} of ${r.total} cells here changed regime over the years. `
  note += 'Regimes blend into each other at their edges, so treat the borders as approximate. Click any square on the map for its story.'
  html += '<div class="panel-foot">' +
    `<a class="button" href="${CONFIG.files.regimes}" download="fire_regimes.geojson">Download regime map (GeoJSON)</a><p>${note}</p></div>`
  body.innerHTML = html
}

function regimeStory(p) {
  const s = regimes && regimes.summary && (regimes.summary.regimes || []).find(r => r.regime_id === p.regime_id)
  const color = regimeColor(p.regime_id, p.color)
  let html = `<div class="story"><p class="story-name"><i style="background:${color}"></i>${p.regime_name}</p>`
  if (s && s.summary) html += `<p>${s.summary}</p>`
  if (s && s.monthly_curve) html += `<div class="story-curve">${monthShape(s.monthly_curve, color)}</div>`
  if (p.hotspots_per_year !== undefined) html += `<p class="dim">This square: about ${formatNumber(p.hotspots_per_year)} hotspots a year${p.peak_month ? `, peak in ${MONTHS[p.peak_month - 1]}` : ''}.</p>`
  if (p.shifted) html += `<p class="story-shift">Changed over time: was "${p.shift_from}", now "${p.shift_to}".</p>`
  return html + '</div>'
}

// ---------------------------------------------------------------------------
// Forecast and About panels (drawn once; they do not depend on the area)
// ---------------------------------------------------------------------------
function renderForecast() {
  const f = forecastData
  // the Forecast tab only appears when there is a real forecast file
  const real = f && !f.is_demo && f.days && f.days.length
  $('forecastTab').hidden = !real
  if (!real) return
  $('forecastSub').textContent = `${f.unit || 'Hotspots per day'}.`
  let html =
    '<div class="chart-key"><span><i class="key-usual is-solid"></i>Expected</span><span><i class="key-band"></i>Likely range</span><span><i class="key-dot"></i>Actual</span></div>' +
    '<div class="chart" id="forecastChart"></div>'
  // the newest forecast, if the file has one
  if (f.live) {
    const tile = (label, part) => part
      ? `<li><p class="check-value">${formatNumber(part.expected)}<span>hotspots</span></p><p class="check-label">${label}</p><p class="check-detail">Likely between ${formatNumber(part.low)} and ${formatNumber(part.high)}</p></li>`
      : ''
    html += `<ul class="checks">${tile('Next 7 days', f.live.next_7_days)}${tile('Next 30 days', f.live.next_30_days)}</ul>` +
      `<p class="note">Forecast made on ${f.live.made_on}.</p>`
  }
  if (f.note) html += `<p class="note">${f.note}</p>`
  $('forecastBody').innerHTML = html
  drawForecastChart($('forecastChart'), f)
}

function renderValidation() {
  const v = validationData
  const real = v && !v.is_demo && v.metrics && v.metrics.length
  $('validationPanel').hidden = !real
  if (!real) return
  $('validationSub').textContent = (v.note || '') + (v.overlap_period ? ` Overlap period: ${v.overlap_period}.` : '')
  $('validationBody').innerHTML = '<ul class="checks">' + v.metrics.map(m =>
    `<li><p class="check-value">${m.value}<span>${m.unit || ''}</span></p><p class="check-label">${m.label}</p>${m.detail ? `<p class="check-detail">${m.detail}</p>` : ''}</li>`).join('') + '</ul>'
}

// ---------------------------------------------------------------------------
// Pages and panels
// ---------------------------------------------------------------------------
function markSidebar() {
  document.querySelectorAll('.side-item').forEach(b => {
    const on = feature ? b.dataset.feature === feature : b.dataset.page === page
    b.setAttribute('aria-current', on ? 'true' : 'false')
  })
}

// the panel beside the map: which group of sections is showing, and its title
function renderPanel() {
  $('featureWindow').hidden = !feature
  if (!feature) return
  $('featureTitle').textContent = PANELS[feature]
  const empty = NEEDS_AREA.includes(feature) && !state.hasArea
  $('featureEmpty').hidden = !empty
  let where = ''
  if (feature === 'forecast' && forecastData) where = forecastData.region || ''
  else if (!empty && NEEDS_AREA.includes(feature) && ready) where = `${areaTitle()} · ${state.fromYear} to ${state.toYear}`
  $('featureWhere').textContent = where
  document.querySelectorAll('.pw-group').forEach(group => {
    group.classList.toggle('is-open', !empty && group.dataset.group.split(' ').includes(feature))
  })
}

// Go to a page, and open a panel (or none). All navigation comes through here.
function show(nextPage, nextFeature = null) {
  if (nextPage === 'home') nextFeature = null
  const pageChanges = nextPage !== page
  const panelOpensOrCloses = !!nextFeature !== !!feature

  const change = () => {
    if (feature === 'listen' && nextFeature !== 'listen') listening?.stop()
    page = nextPage
    feature = nextFeature
    document.body.dataset.page = page
    flames.setActive(page === 'home')
    // Patterns are the fire regimes, so the map switches to that layer (and back for the others)
    if (feature === 'patterns' && regimes && state.layer !== 'regimes') update({ layer: 'regimes' })
    else if (['trends', 'anomalies', 'listen'].includes(feature) && state.layer !== 'hotspots') update({ layer: 'hotspots' })
    renderPanel()
    markSidebar()
    writeAddress()
    if (pageChanges) window.scrollTo(0, 0)
    if (nextFeature) $('featureBody').scrollTop = 0
    // the map just changed size (new page, or the panel took some of its room): fit it again
    if (page === 'map' && (pageChanges || panelOpensOrCloses)) {
      mapView.refresh()
      if (state.hasArea) { mapView.setArea(state.area); mapView.fitArea() }
      else if (pageChanges) mapView.showWorld()
      requestAnimationFrame(() => {
        if (page !== 'map') return
        mapView.refresh()
        if (state.hasArea) mapView.fitArea()
      })
    }
    if (page === 'map' && pageChanges) showTip(false)
  }

  // moving between Home and the map, the browser animates the logo into the corner
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (pageChanges && document.startViewTransition && !calm) document.startViewTransition(change)
  else change()
}

function openFeature(name) { show('map', name) }
function closeFeature() { show(page, null) }

// a short tip on the map that fades away (shown once, or again from the menu)
const MAP_TIP = 'Click the map to pick a place. Drag the box to move it, or a corner to resize it. Shift + drag draws a new box.'
let tipTimer = null
function showTip(force, text = MAP_TIP) {
  if (!force && loadPref('tipSeen', false)) return
  const tip = $('mapHint')
  tip.textContent = text
  tip.hidden = false
  requestAnimationFrame(() => tip.classList.add('is-shown'))
  clearTimeout(tipTimer)
  tipTimer = setTimeout(() => { tip.classList.remove('is-shown'); setTimeout(() => { tip.hidden = true }, 400) }, 6500)
  if (text === MAP_TIP) savePref('tipSeen', true)
}

// ---------------------------------------------------------------------------
// Places: search results, favourites, suggestions
// ---------------------------------------------------------------------------
// Show a place that was searched for or picked from a chip.
function goTo(place, precise = false) {
  // no outline (a town, a point): use a box around it
  let [s, w, n, e] = (precise ? voicePlaceBounds(place) : place.box) || [place.lat - 3, place.lon - 5, place.lat + 3, place.lon + 5]
  // make small places big enough to hold a few grid squares
  const minLat = precise ? 0.01 : CONFIG.cellSize * 2, minLon = precise ? 0.01 : CONFIG.cellSize * 3
  if (n - s < minLat) { const mid = (s + n) / 2; s = mid - minLat / 2; n = mid + minLat / 2 }
  if (e - w < minLon) { const mid = (w + e) / 2; w = mid - minLon / 2; e = mid + minLon / 2 }
  const scale = precise ? 10000 : 10
  const round = v => Math.round(v * scale) / scale
  const area = [round(Math.max(-89, s)), round(Math.max(-180, w)), round(Math.min(89, n)), round(Math.min(180, e))]
  update({ area, label: place.name === 'Custom area' ? null : place.name, month: null })
  show('map', feature)
  mapView.fitArea()
}

const savedPlaces = () => [...settings.favourites, ...CONFIG.presets]
  .map(p => ({ name: p.name, detail: settings.favourites.includes(p) ? 'Favourite' : 'Suggested', box: p.box, lat: (p.box[0] + p.box[2]) / 2, lon: (p.box[1] + p.box[3]) / 2 }))

const isFavourite = () => settings.favourites.some(f => sameBox(f.box, state.area))

function drawStar() {
  const on = state.hasArea && isFavourite()
  $('starButton').setAttribute('aria-pressed', String(on))
  $('starButton').title = on ? 'Remove from favourites' : 'Add to favourites'
}

function toggleFavourite() {
  if (isFavourite()) settings.favourites = settings.favourites.filter(f => !sameBox(f.box, state.area))
  else settings.favourites = [...settings.favourites, { name: areaTitle(), box: [...state.area] }]
  savePref('favourites', settings.favourites)
  drawStar()
  drawPlaceChips()
}

// Home: favourites under the search box, or suggested places if there are none yet
function drawPlaceChips() {
  const chips = $('placeChips')
  const star = '<svg viewBox="0 0 16 16" aria-hidden="true"><use href="#i-star"/></svg>'
  if (settings.favourites.length) {
    $('placesLabel').textContent = 'Favourites'
    chips.innerHTML = settings.favourites.map((f, i) =>
      `<span class="chip is-favourite"><button type="button" data-go="${i}">${star}${f.name}</button>` +
      `<button type="button" class="chip-remove" data-remove="${i}" title="Remove ${f.name}"><svg viewBox="0 0 16 16" aria-hidden="true"><use href="#i-close"/></svg><span class="visually-hidden">Remove ${f.name}</span></button></span>`).join('')
  } else {
    $('placesLabel').textContent = 'Suggested'
    chips.innerHTML = CONFIG.presets.map((p, i) => `<button type="button" class="chip" data-go="${i}">${p.name}</button>`).join('')
  }
  const list = settings.favourites.length ? settings.favourites : CONFIG.presets
  chips.querySelectorAll('[data-go]').forEach(b => {
    b.onclick = () => { const p = list[Number(b.dataset.go)]; goTo({ name: p.name, box: p.box }) }
  })
  chips.querySelectorAll('[data-remove]').forEach(b => {
    b.onclick = () => {
      settings.favourites = settings.favourites.filter((_, i) => i !== Number(b.dataset.remove))
      savePref('favourites', settings.favourites)
      drawStar()
      drawPlaceChips()
    }
  })
}

// ---------------------------------------------------------------------------
// Settings: dark mode, colour-blind colours, spoken descriptions, the side menu
// ---------------------------------------------------------------------------
function applySettings() {
  document.documentElement.dataset.theme = settings.dark ? 'dark' : 'light'
  document.body.classList.toggle('side-closed', settings.sideClosed)
  $('sideToggle').setAttribute('aria-expanded', String(!settings.sideClosed))
  document.querySelectorAll('[data-toggle]').forEach(b => b.setAttribute('aria-pressed', String(!!settings[b.dataset.toggle])))
  setPressed('[data-basemap]', 'basemap', settings.basemap)
  $('imageryKey').hidden = $('imageryCaption').hidden = settings.basemap !== 'nasa'
}

function toggleSetting(name) {
  settings[name] = !settings[name]
  if (name === 'dark') {
    saveTheme(settings.dark ? 'dark' : 'light')
    applySettings()
    mapView.setDark(settings.dark)
    flames.redraw()
  } else {
    savePref(name, settings[name])
    applySettings()
  }
  if (name === 'safeColors') {
    setPalette(settings.safeColors ? 'safe' : 'fire')
    mapView.redrawColors()
    render()
  }
  if (name === 'speech') {
    $('audioSpeech').checked = settings.speech
    listening?.stop()
  }
}

// ---------------------------------------------------------------------------
// Buttons, menus and inputs
// ---------------------------------------------------------------------------
function flash(button, text) {
  const label = button.querySelector('span') || button
  const before = label.textContent
  label.textContent = text
  setTimeout(() => { label.textContent = before }, 1600)
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

function stopPlaying() {
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
  }, stopPlaying)
}

// Shared by navigation and microphone actions.
function closeMenuOnPhone() {
  if (window.innerWidth < 760 && !settings.sideClosed) toggleSetting('sideClosed')
}

// Things that work before the data file has loaded: moving around, search, settings.
function wireShell() {
  // side menu, Home buttons, and the logo
  // on a phone the menu covers the page, so it closes again once you have picked something
  document.querySelectorAll('button[data-page]').forEach(b => {
    b.onclick = () => { closeMenuOnPhone(); show(b.dataset.page) }
  })
  document.querySelectorAll('[data-feature]').forEach(b => {
    b.onclick = () => { closeMenuOnPhone(); feature === b.dataset.feature ? closeFeature() : openFeature(b.dataset.feature) }
  })
  $('featureClose').onclick = closeFeature
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && feature && !event.target.closest('input, select')) closeFeature()
  })

  $('sideToggle').onclick = () => toggleSetting('sideClosed')
  document.querySelectorAll('[data-toggle]').forEach(b => { b.onclick = () => toggleSetting(b.dataset.toggle) })
  document.querySelectorAll('[data-basemap]').forEach(b => {
    b.onclick = () => {
      settings.basemap = b.dataset.basemap
      savePref('basemap', settings.basemap)
      applySettings()
      mapView.setBasemap(settings.basemap)
    }
  })

  // place search (the big box on Home and the small one in the top bar)
  const searchOptions = { getSaved: savedPlaces, getOffline: () => mapView.getPlaces(), onPick: goTo }
  wireSearch($('heroSearch'), $('heroResults'), searchOptions)
  wireSearch($('topSearch'), $('topResults'), searchOptions)
  drawPlaceChips()
  $('starButton').onclick = toggleFavourite

  // custom area: typed edges, or draw it on the map
  const areaForm = $('areaForm'), customButton = $('customAreaButton')
  const openAreaForm = open => { areaForm.hidden = !open; customButton.setAttribute('aria-expanded', String(open)) }
  customButton.onclick = () => { openAreaForm(areaForm.hidden); if (!areaForm.hidden) $('north').focus() }
  areaForm.onsubmit = event => {
    event.preventDefault()
    const inputs = [$('south'), $('west'), $('north'), $('east')]
    const [s, w, n, e] = inputs.map(input => Number(input.value))
    let problem = ''
    if (![s, w, n, e].every(Number.isFinite) || inputs.some(input => input.value.trim() === '')) problem = 'Enter a number in all four boxes. West of Greenwich and south of the equator are negative, for example West = -119.'
    else if (n <= s) problem = 'North must be a bigger number than South.'
    else if (e <= w) problem = 'East must be a bigger number than West. For western Canada both are negative, for example West = -119 and East = -110.'
    else if (s < -90 || n > 90 || w < -180 || e > 180) problem = 'Latitude goes from -90 to 90 and longitude from -180 to 180.'
    $('areaError').hidden = !problem
    $('areaError').textContent = problem
    if (problem) return
    openAreaForm(false)
    goTo({ name: 'Custom area', box: [s, w, n, e] })
  }
  $('drawButton').onclick = () => {
    openAreaForm(false)
    show('map', feature)
    // wait for the map page to be on screen before drawing on it
    setTimeout(() => {
      mapView.startDrawing()
      showTip(true, 'Drag on the map to draw your area. Press Esc to cancel.')
    }, 500)
  }

  // the ⋮ menu
  const menu = $('appMenu'), menuButton = $('menuButton'), exportPanel = $('exportPanel')
  const closeMenu = () => { menu.hidden = true; exportPanel.hidden = true; menuButton.setAttribute('aria-expanded', 'false') }
  menuButton.onclick = () => {
    const open = menu.hidden && exportPanel.hidden
    closeMenu()
    menu.hidden = !open
    menuButton.setAttribute('aria-expanded', String(open))
  }
  document.addEventListener('click', event => {
    if (!event.target.closest('.kebab')) closeMenu()
    if (!event.target.closest('.search-hero')) openAreaForm(false)
  })
  document.addEventListener('keydown', event => { if (event.key === 'Escape') { closeMenu(); openAreaForm(false) } })

  $('shareButton').onclick = async () => {
    const ok = await copyText(location.href)
    flash($('shareButton'), ok ? 'Link copied' : 'Copy the address bar instead')
  }
  $('tipButton').onclick = () => { closeMenu(); show('map', feature); showTip(true) }
  $('exportButton').onclick = () => {
    menu.hidden = true
    exportPanel.hidden = false
    const rows = ready && state.hasArea ? currentCsv().rows : 0
    const picked = focus()
    const period = picked ? `${MONTHS[picked.month]} ${picked.year}` : `${state.fromYear} to ${state.toYear}`
    $('exportText').textContent = !ready ? 'The data is still loading.'
      : !state.hasArea ? 'Pick a place first: search, or click the map.'
      : rows ? `${formatNumber(rows)} rows: every grid square and month with hotspots in ${areaName() === 'Custom area' ? 'the selected area' : areaName()}, ${period}.`
      : 'There are no hotspots in this area and period, so there is nothing to download.'
    $('downloadButton').disabled = $('copyCsvButton').disabled = !rows
  }
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

  // the two-way switches
  document.querySelectorAll('[data-metric]').forEach(b => { b.onclick = () => update({ metric: b.dataset.metric }) })
  document.querySelectorAll('[data-mode]').forEach(b => { b.onclick = () => update({ mode: b.dataset.mode }) })
  document.querySelectorAll('[data-layer]').forEach(b => { b.onclick = () => update({ layer: b.dataset.layer }) })
}

// Things that need the data file: years, the time slider, sound.
function wireData() {
  // time slider on the map: 0 = the selected years, then one step per year
  const slider = $('mapYear')
  slider.max = data.lastYear - data.firstYear + 1
  slider.oninput = () => {
    const v = Number(slider.value)
    update({ mapYear: v ? data.firstYear + v - 1 : null, month: null })
  }
  $('mapPlay').onclick = () => {
    if (isPlaying()) { stopPlaying(); return }
    startPlaying()
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

  // The Listen panel shares month selection with the calendar and the map.
  listening = createListeningPanel({
    onNavigate: createVoiceNavigator({
      resolvePlace: (query, signal, alternatives) => resolveSpokenPlace(query, {getSaved:savedPlaces, getOffline:()=>mapView.getPlaces(), signal, alternatives}),
      apply: ({place,fromYear,toYear}) => {
        if(fromYear!==null) update({fromYear,toYear,month:null,mapYear:null})
        if(place) goTo(place, true)
      },
    }),
    onOpen: () => openFeature('listen'),
    onVoiceStart: () => { if (isPlaying()) stopPlaying() },
    onSpeechChange: enabled => { settings.speech = enabled; savePref('speech', enabled); applySettings() },
    onMetric: metric => update({ metric }),
    onPick: (year, month) => { if (isPlaying()) stopPlaying(); update({ month: monthKey(year, month) }, true) },
    onMode: mode => update({ mode }),
    onHighlight: (row, month) => markPlaying($('calendar'), row, month),
  })
  // the calendar's Listen button opens the Listen panel
  $('listenButton').onclick = () => openFeature('listen')
  // "Spoken descriptions" on Home and the tick box in the Listen panel are the same setting
  const speechBox = $('audioSpeech')
  if (speechBox.disabled) {
    document.querySelectorAll('[data-toggle="speech"]').forEach(b => { b.disabled = true; b.title = 'This browser cannot speak text' })
  } else {
    speechBox.checked = settings.speech
    speechBox.addEventListener('change', () => {
      settings.speech = speechBox.checked
      savePref('speech', settings.speech)
      applySettings()
    })
  }
}

// ---------------------------------------------------------------------------
// Chat box: a text summary of what is on screen, sent along with each question
// ---------------------------------------------------------------------------
function describeView() {
  if (!ready) return 'The data is still loading.'
  if (!state.hasArea) return 'No place is selected yet. The user needs to search for a place or click the map first.'
  const noTags = html => html.replace(/<[^>]+>/g, '')
  const totals = monthlyTotals(state.area)
  const counts = buildTable(totals, 'count', state.fromYear, state.toYear)
  const energy = buildTable(totals, 'frp', state.fromYear, state.toYear)
  const lines = []

  lines.push(`Area: ${areaName()} (${areaText()})`)
  lines.push(`Years shown: ${state.fromYear} to ${state.toYear}`)
  lines.push(`Measure picked on screen: ${state.metric === 'frp' ? 'fire energy (FRP)' : 'hotspot count'}`)
  lines.push(`Record: ${CONFIG.observedLabel}, on a ${CONFIG.cellSize} degree grid`)
  if (cellsInArea(state.area).reduce((a, b) => a + b, 0) === 0) {
    lines.push('There is no data inside this area.')
    return lines.join('\n')
  }
  lines.push(`Headline on screen: ${noTags(overviewVerdict(table).sentence)}`)
  if (state.month) {
    const picked = focus()
    lines.push(`Selected month: ${MONTHS[picked.month]} ${picked.year}. Headline: ${noTags(monthVerdict(table, picked.year, picked.month).sentence)}`)
  }

  lines.push('')
  lines.push('Usual month (median over all years shown): month = hotspots, fire energy')
  for (let m = 0; m < 12; m++) {
    lines.push(`${MONTHS[m]} = ${formatNumber(counts.usual[m].median)}, ${formatPower(energy.usual[m].median)}`)
  }

  lines.push('')
  lines.push('Every month: year-month = hotspots, fire energy, score in sigma (compared with the same month in other years)')
  counts.years.forEach((year, row) => {
    for (let m = 0; m < 12; m++) {
      const n = counts.value[row][m]
      if (Number.isNaN(n)) continue
      lines.push(`${monthKey(year, m)} = ${formatNumber(n)}, ${formatPower(energy.value[row][m])}, ${formatScore(counts.score[row][m])}`)
    }
  })

  lines.push('')
  lines.push('Yearly totals: year = hotspots, fire energy')
  counts.years.forEach((year, row) => {
    lines.push(`${year} = ${formatNumber(counts.yearTotal[row])}, ${formatPower(energy.yearTotal[row])}${counts.yearComplete[row] ? '' : ' (year not complete)'}`)
  })

  if (regimes) {
    const r = regimesInArea()
    if (r.total) {
      lines.push('')
      lines.push(`Fire regimes in this area (${r.total} grid cells, ${r.shifted} changed regime over the years):`)
      for (const [id, n] of r.sorted) {
        const s = r.summaries.get(id)
        lines.push(`- ${r.names.get(id).regime_name}: ${Math.round(n / r.total * 100)}% of cells. ${s && s.summary ? s.summary : ''}`)
      }
    }
  }

  if (forecastData && forecastData.days && forecastData.days.length) {
    lines.push('')
    lines.push(`Forecast (${forecastData.region || 'whole data region'}, ${forecastData.unit || 'hotspots per day'}):`)
    lines.push(JSON.stringify(forecastData.days))
    if (forecastData.live) lines.push(`Newest forecast: ${JSON.stringify(forecastData.live)}`)
  }
  if (validationData && validationData.metrics && validationData.metrics.length) {
    lines.push('')
    lines.push('Check that MODIS and VIIRS agree:')
    for (const m of validationData.metrics) lines.push(`- ${m.label}: ${m.value}${m.unit || ''}. ${m.detail || ''}`)
  }
  return lines.join('\n')
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
async function start() {
  // A reopened or refreshed page always starts at Home, without an old selection.
  try { history.replaceState(null, '', location.pathname + location.search) } catch {}
  window.addEventListener('pageshow', event => {
    // Back/forward cache can restore the entire old page without re-running startup.
    if (event.persisted) location.reload()
  })
  document.body.dataset.page = page

  setPalette(settings.safeColors ? 'safe' : 'fire')
  flames = startFlames($('flames'))
  flames.setActive(page === 'home')

  mapView = createMap($('map'), $('mapLegend'), {
    onAreaChange: area => update({ area }),
    onRegimeClick: (props, latlng) => mapView.openPopup(regimeStory(props), latlng),
  })
  mapView.setDark(settings.dark)
  mapView.setBasemap(settings.basemap)
  mapView.loadPlaces()   // not awaited: only the offline search needs it

  applySettings()
  wireShell()
  initChat(describeView)
  render()
  markSidebar()

  try {
    await loadHotspots()
  } catch (err) {
    $('homeStatus').textContent = `The satellite record did not load: ${err.message}. If you opened index.html by double-clicking it, start a local server instead (see README).`
    $('verdict').innerHTML = `<p class="loading">${$('homeStatus').textContent}</p>`
    return
  }

  state.fromYear = data.firstYear
  state.toYear = data.lastYear
  $('homeStatus').textContent = `NASA satellite hotspot detections for the whole world, ${data.firstYear} to ${data.lastYear}.`
  wireData()
  mapView.buildCells()
  ready = true
  render()

  // optional files: a panel simply stays hidden or explains what to add if its file is missing
  const [geojson, summary, forecast, validation] = await Promise.all([
    loadJson(CONFIG.files.regimes), loadJson(CONFIG.files.regimeSummary),
    loadJson(CONFIG.files.forecast), loadJson(CONFIG.files.validation),
  ])
  forecastData = forecast
  validationData = validation
  if (geojson && geojson.features && geojson.features.length) {
    regimes = { geojson, summary }
    mapView.setRegimes(geojson)
  } else {
    state.layer = 'hotspots'
    document.querySelector('[data-layer="regimes"]').disabled = true
  }
  renderForecast()
  renderValidation()
  render()

  // a link that opens straight on the map: size the map once the layout has settled
  if (page === 'map') requestAnimationFrame(() => setTimeout(() => {
    mapView.refresh()
    if (state.hasArea) mapView.fitArea()
  }, 60))

  window.addEventListener('hashchange', () => {
    listening?.stop()
    readAddress(); readYears()
    document.body.dataset.page = page
    flames.setActive(page === 'home')
    markSidebar()
    render()
  })
}

start()
