// The burning calendar: one row per year, one square per month.

import { CONFIG, MONTHS, MONTHS_SHORT } from './config.js'
import { fireBreaks, fireColor, scoreColor, isDark, FIRE_RAMP, SCORE_STEPS } from './colors.js'
import { formatValue, formatScore } from './stats.js'
import { showTip, hideTip } from './tooltip.js'

// table: from buildTable().  mode: 'amount' or 'score'.
// focus: { year, month } or null.  onPick(year, month) is called on click.
export function drawCalendar(element, legendElement, table, mode, focus, onPick) {
  const breaks = fireBreaks(table.value.flat().filter(v => !Number.isNaN(v)))
  const unit = table.metric === 'frp' ? 'fire energy' : 'hotspots'
  const maxUsual = Math.max(...table.usual.map(u => u.median), 1e-9)
  const maxYear = Math.max(...table.yearTotal, 1e-9)

  let html = ''

  // strip above the grid: what a usual year looks like
  html += '<div class="cal-row cal-usual" aria-hidden="true"><span class="cal-side">Usual<br>year</span>'
  table.usual.forEach(u => {
    html += `<span class="cal-usual-bar"><i style="height:${Math.round(u.median / maxUsual * 100)}%"></i></span>`
  })
  html += '<span></span></div>'

  html += '<div class="cal-row cal-head" aria-hidden="true"><span></span>'
  MONTHS_SHORT.forEach(m => { html += `<span>${m}</span>` })
  html += '<span class="cal-total-head">Year total</span></div>'

  table.years.forEach((year, row) => {
    const estimated = table.yearEstimated[row] >= 0.5
    const wasEstimated = row > 0 && table.yearEstimated[row - 1] >= 0.5
    if (row > 0 && wasEstimated && !estimated) {
      html += `<div class="cal-divide"><span>Real VIIRS observations from ${year}. Earlier years are estimated from MODIS.</span></div>`
    }

    html += `<div class="cal-row${estimated ? ' is-estimated' : ''}" role="row"><span class="cal-year">${year}</span>`
    for (let m = 0; m < 12; m++) {
      const v = table.value[row][m]
      if (Number.isNaN(v)) {
        html += `<button type="button" class="cal-cell is-missing" data-row="${row}" data-month="${m}" aria-label="${MONTHS[m]} ${year}: no data available" aria-disabled="true"></button>`
        continue
      }
      const z = table.score[row][m]
      const color = mode === 'score'
        ? (table.usual[m].max === 0 ? null : scoreColor(z))
        : fireColor(v, breaks)
      const unusual = z >= CONFIG.unusualSigma && v > 0
      const picked = focus && focus.year === year && focus.month === m
      const classes = ['cal-cell']
      if (!color) classes.push('is-empty')
      if (color && isDark(color)) classes.push('is-dark')
      if (unusual) classes.push('is-unusual')
      if (picked) classes.push('is-picked')
      html += `<button type="button" class="${classes.join(' ')}" data-row="${row}" data-month="${m}"` +
        `${color ? ` style="background:${color}"` : ''} aria-pressed="${picked ? 'true' : 'false'}"` +
        ` aria-label="${MONTHS[m]} ${year}: ${formatValue(v, table.metric)} ${unit}, score ${formatScore(z)}${unusual ? ', unusual' : ''}"></button>`
    }
    const share = table.yearTotal[row] / maxYear
    html += `<span class="cal-total${table.yearComplete[row] ? '' : ' is-partial'}">` +
      `<i style="--share:${share.toFixed(3)}"></i><b>${formatValue(table.yearTotal[row], table.metric)}${table.yearComplete[row] ? '' : ' so far'}</b></span>`
    html += '</div>'
  })

  element.innerHTML = html

  // --- legend -------------------------------------------------------------
  let legend = ''
  if (mode === 'score') {
    legend += '<div class="legend-ramp is-score">' + SCORE_STEPS.map(s =>
      `<span><i style="background:${s.color}"></i>${s.label}</span>`).join('') + '</div>' +
      '<p class="legend-note">than usual for that calendar month</p>'
  } else {
    const step = breaks.length > 1 ? (FIRE_RAMP.length - 1) / (breaks.length - 1) : 0
    legend += '<div class="legend-ramp">' + breaks.map((b, i) =>
      `<span><i style="background:${FIRE_RAMP[Math.round(i * step)]}"></i>${formatValue(b, table.metric)}${i === breaks.length - 1 ? '+' : ''}</span>`).join('') + '</div>' +
      `<p class="legend-note">${unit} in the month</p>`
  }
  legend += `<p class="legend-key"><i class="key-unusual"></i>Unusual month (${CONFIG.unusualSigma}σ or more above its usual)</p>`
  if (table.yearEstimated.some(e => e >= 0.5)) {
    legend += '<p class="legend-key"><i class="key-estimated"></i>Estimated from MODIS</p>'
  }
  legendElement.innerHTML = legend

  // --- interaction ----------------------------------------------------------
  element.onclick = event => {
    const cell = event.target.closest('button.cal-cell')
    if (cell && cell.getAttribute('aria-disabled') !== 'true') onPick(table.years[Number(cell.dataset.row)], Number(cell.dataset.month))
  }

  const describe = cell => {
    const row = Number(cell.dataset.row), m = Number(cell.dataset.month)
    const v = table.value[row][m], u = table.usual[m]
    if (!Number.isFinite(v)) return `<b>${MONTHS[m]} ${table.years[row]}</b><br>No data available; this is not zero activity.`
    let text = `<b>${MONTHS[m]} ${table.years[row]}</b>` +
      `<br>${formatValue(v, table.metric)} ${unit}` +
      `<br>Usual ${MONTHS[m]}: ${formatValue(u.median, table.metric)}`
    if (u.max > 0) text += `<br>Score ${formatScore(table.score[row][m])}, rank ${table.rank[row][m]} of ${u.years}`
    if (table.estimatedShare[row][m] >= 0.5) text += '<br><span class="dim">Estimated from MODIS</span>'
    return text
  }
  element.onmouseover = event => {
    const cell = event.target.closest('button.cal-cell')
    if (cell) showTip(describe(cell), cell)
  }
  element.onmouseout = event => { if (event.target.closest('button.cal-cell')) hideTip() }
  element.onfocusin = event => {
    const cell = event.target.closest('button.cal-cell')
    if (cell) showTip(describe(cell), cell)
  }
  element.onfocusout = hideTip

  // arrow keys move between months
  element.onkeydown = event => {
    const cell = event.target.closest('button.cal-cell')
    if (!cell) return
    const move = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] }[event.key]
    if (!move) return
    event.preventDefault()
    let row = Number(cell.dataset.row) + move[0], m = Number(cell.dataset.month) + move[1]
    const next = element.querySelector(`button.cal-cell[data-row="${row}"][data-month="${m}"]`)
    if (next) next.focus()
  }
}

// used by the sound player to show which month is being played
export function markPlaying(element, row, month) {
  const old = element.querySelector('.is-playing')
  if (old) old.classList.remove('is-playing')
  if (row === null) return
  const cell = element.querySelector(`button.cal-cell[data-row="${row}"][data-month="${month}"]`)
  if (cell) cell.classList.add('is-playing')
}
