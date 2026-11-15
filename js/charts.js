// Small hand-made SVG charts. No chart library, so there is nothing to install.

import { MONTHS, MONTHS_SHORT } from './config.js'
import { formatValue, formatNumber } from './stats.js'
import { showTip, hideTip } from './tooltip.js'

const YEAR_COLOR = '#D6531F'   // the selected year
const USUAL_COLOR = '#9AA8A0'  // the usual year

// round the top of the y axis up to a tidy number, and say how many equal
// steps divide it into round numbers. Returns [top, steps].
function tidyAxis(v) {
  if (v <= 0) return [1, 4]
  const power = Math.pow(10, Math.floor(Math.log10(v)))
  for (const [lead, steps] of [[1, 4], [2, 4], [2.5, 5], [4, 4], [5, 5], [8, 4], [10, 4]]) {
    if (v <= lead * power) return [lead * power, steps]
  }
  return [10 * power, 4]
}

function linePath(points) {
  return points.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ')
}

// ---------------------------------------------------------------------------
// One year against a usual year
// ---------------------------------------------------------------------------
export function drawSeasonChart(element, table, year) {
  const W = 560, H = 236, left = 50, right = 18, top = 22, bottom = 28
  const row = year - table.years[0]
  const values = table.value[row]
  const known = values.filter(v => !Number.isNaN(v))
  const [topValue, steps] = tidyAxis(Math.max(...known, ...table.usual.map(u => u.q75), 1))
  const x = m => left + m / 11 * (W - left - right)
  const y = v => top + (1 - v / topValue) * (H - top - bottom)

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${year} month by month against a usual year">`

  // grid lines and y labels
  for (let i = 0; i <= steps; i++) {
    const v = topValue * i / steps
    svg += `<line class="grid" x1="${left}" x2="${W - right}" y1="${y(v)}" y2="${y(v)}"/>` +
      `<text class="axis" x="${left - 8}" y="${y(v) + 4}" text-anchor="end">${formatValue(v, table.metric)}</text>`
  }
  MONTHS_SHORT.forEach((name, m) => {
    svg += `<text class="axis" x="${x(m)}" y="${H - 8}" text-anchor="middle">${name[0]}</text>`
  })

  // band: the middle half of all years
  const upper = table.usual.map((u, m) => [x(m), y(u.q75)])
  const lower = table.usual.map((u, m) => [x(m), y(u.q25)]).reverse()
  svg += `<path class="band" d="${linePath(upper)} ${linePath(lower).replace('M', 'L')} Z"/>`

  // usual year
  svg += `<path d="${linePath(table.usual.map((u, m) => [x(m), y(u.median)]))}" fill="none" stroke="${USUAL_COLOR}" stroke-width="1.5" stroke-dasharray="5 4"/>`

  // the selected year
  const points = []
  values.forEach((v, m) => { if (!Number.isNaN(v)) points.push([x(m), y(v)]) })
  svg += `<path d="${linePath(points)}" fill="none" stroke="${YEAR_COLOR}" stroke-width="2" stroke-linejoin="round"/>`
  points.forEach(p => { svg += `<circle cx="${p[0]}" cy="${p[1]}" r="3.5" fill="${YEAR_COLOR}" stroke="var(--panel)" stroke-width="2"/>` })

  // label the year at its highest point
  let peak = 0
  values.forEach((v, m) => { if (!Number.isNaN(v) && v > (values[peak] || 0)) peak = m })
  const anchor = peak > 8 ? 'end' : 'start'
  svg += `<text class="mark" x="${x(peak) + (anchor === 'end' ? -9 : 9)}" y="${Math.max(top - 6, y(values[peak]) - 6)}" text-anchor="${anchor}">${year}</text>`

  svg += `<line class="cross" y1="${top}" y2="${H - bottom}" visibility="hidden"/>`
  svg += `<rect class="hit" x="${left}" y="0" width="${W - left - right}" height="${H}" fill="transparent"/>`
  svg += '</svg>'
  element.innerHTML = svg

  // hover: a vertical line and a tip with both numbers
  const hit = element.querySelector('.hit'), cross = element.querySelector('.cross')
  hit.onmousemove = event => {
    const box = hit.getBoundingClientRect()
    const m = Math.max(0, Math.min(11, Math.round((event.clientX - box.left) / box.width * 11)))
    cross.setAttribute('x1', x(m)); cross.setAttribute('x2', x(m)); cross.setAttribute('visibility', 'visible')
    const v = values[m]
    showTip(`<b>${MONTHS[m]}</b><br>${year}: ${Number.isNaN(v) ? 'no data yet' : formatValue(v, table.metric)}` +
      `<br>Usual: ${formatValue(table.usual[m].median, table.metric)}` +
      `<br><span class="dim">Middle half of years: ${formatValue(table.usual[m].q25, table.metric)} to ${formatValue(table.usual[m].q75, table.metric)}</span>`,
      { x: event.clientX, y: event.clientY - 8 })
  }
  hit.onmouseleave = () => { cross.setAttribute('visibility', 'hidden'); hideTip() }
}

// ---------------------------------------------------------------------------
// Forecast: expected against actual, day by day
// ---------------------------------------------------------------------------
export function drawForecastChart(element, forecast) {
  const days = forecast.days
  const W = 560, H = 210, left = 50, right = 18, top = 24, bottom = 28
  const [topValue, steps] = tidyAxis(Math.max(...days.map(d => Math.max(d.high ?? d.expected, d.actual ?? 0))))
  const x = i => left + i / (days.length - 1) * (W - left - right)
  const y = v => top + (1 - v / topValue) * (H - top - bottom)
  const shortDate = text => {
    const d = new Date(text + 'T00:00:00Z')
    return `${MONTHS_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}`
  }

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Expected and actual hotspots per day">`
  for (let i = 0; i <= steps; i++) {
    const v = topValue * i / steps
    svg += `<line class="grid" x1="${left}" x2="${W - right}" y1="${y(v)}" y2="${y(v)}"/>` +
      `<text class="axis" x="${left - 8}" y="${y(v) + 4}" text-anchor="end">${formatNumber(v)}</text>`
  }
  const every = Math.ceil(days.length / 6)
  days.forEach((d, i) => {
    if (i % every === 0) svg += `<text class="axis" x="${x(i)}" y="${H - 8}" text-anchor="middle">${shortDate(d.date)}</text>`
  })

  if (days[0].low !== undefined) {
    const upper = days.map((d, i) => [x(i), y(d.high)])
    const lower = days.map((d, i) => [x(i), y(d.low)]).reverse()
    svg += `<path class="band" d="${linePath(upper)} ${linePath(lower).replace('M', 'L')} Z"/>`
  }
  svg += `<path d="${linePath(days.map((d, i) => [x(i), y(d.expected)]))}" fill="none" stroke="${USUAL_COLOR}" stroke-width="2"/>`
  days.forEach((d, i) => {
    if (d.actual !== undefined) svg += `<circle cx="${x(i)}" cy="${y(d.actual)}" r="3.5" fill="${YEAR_COLOR}" stroke="var(--panel)" stroke-width="1.5"/>`
  })

  // where the forecast was made: everything to the right is the future
  const madeAt = days.findIndex(d => d.date === forecast.made_on)
  if (madeAt >= 0) {
    svg += `<line class="today" x1="${x(madeAt)}" x2="${x(madeAt)}" y1="${top - 6}" y2="${H - bottom}"/>` +
      `<text class="mark" x="${x(madeAt) + 7}" y="${top - 8}">Forecast made ${shortDate(forecast.made_on)}</text>`
  }
  svg += `<line class="cross" y1="${top}" y2="${H - bottom}" visibility="hidden"/>`
  svg += `<rect class="hit" x="${left}" y="0" width="${W - left - right}" height="${H}" fill="transparent"/></svg>`
  element.innerHTML = svg

  const hit = element.querySelector('.hit'), cross = element.querySelector('.cross')
  hit.onmousemove = event => {
    const box = hit.getBoundingClientRect()
    const i = Math.max(0, Math.min(days.length - 1, Math.round((event.clientX - box.left) / box.width * (days.length - 1))))
    const d = days[i]
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible')
    showTip(`<b>${shortDate(d.date)}</b><br>Expected: ${formatNumber(d.expected)}` +
      (d.low !== undefined ? ` <span class="dim">(${formatNumber(d.low)} to ${formatNumber(d.high)})</span>` : '') +
      (d.actual !== undefined ? `<br>Actual: ${formatNumber(d.actual)}` : ''),
      { x: event.clientX, y: event.clientY - 8 })
  }
  hit.onmouseleave = () => { cross.setAttribute('visibility', 'hidden'); hideTip() }
}

// ---------------------------------------------------------------------------
// Tiny 12-month shape, used on the regime cards
// ---------------------------------------------------------------------------
export function monthShape(curve, color) {
  const max = Math.max(...curve, 1e-9)
  const bars = curve.map((v, m) => {
    const h = Math.max(1, v / max * 26)
    return `<rect x="${m * 7}" y="${28 - h}" width="5" height="${h}" rx="1" fill="${color}"/>`
  }).join('')
  return `<svg class="shape" viewBox="0 0 84 34" role="img" aria-label="Share of fire by month">${bars}` +
    '<text x="0" y="34">J</text><text x="42" y="34">J</text><text x="77" y="34">D</text></svg>'
}
