// The numbers behind the calendar and the verdict sentence.
//
// For the selected area we compare each month with the SAME calendar month in
// every other year. Example: May 2023 is compared with every May on record.
//
//   score (sigma) = (this month - average for that calendar month) / standard deviation
//
// If Shriya's anomaly method ends up different, this is the one file to change.

import { CONFIG, MONTHS } from './config.js'
import { data } from './data.js'

function median(sorted) {
  if (!sorted.length) return 0
  const mid = (sorted.length - 1) / 2
  return (sorted[Math.floor(mid)] + sorted[Math.ceil(mid)]) / 2
}
function quantile(sorted, q) {
  if (!sorted.length) return 0
  const pos = (sorted.length - 1) * q
  const lo = Math.floor(pos), hi = Math.ceil(pos)
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo)
}

// totals: what monthlyTotals() returned.  metric: 'count' or 'frp'.
export function buildTable(totals, metric, firstYear, lastYear) {
  const years = []
  for (let y = firstYear; y <= lastYear; y++) years.push(y)

  // value[row][month]; NaN means "the file has no data for that month yet"
  const value = years.map(() => new Array(12).fill(NaN))
  const estimatedShare = years.map(() => new Array(12).fill(0))
  data.months.forEach((key, i) => {
    const y = Number(key.slice(0, 4)), m = Number(key.slice(5, 7)) - 1
    const row = y - firstYear
    if (row < 0 || row >= years.length) return
    value[row][m] = totals[metric][i]
    estimatedShare[row][m] = totals.count[i] > 0 ? totals.estimated[i] / totals.count[i] : NaN
  })

  // what is usual for each calendar month
  const usual = []
  for (let m = 0; m < 12; m++) {
    const values = value.map(row => row[m]).filter(v => !Number.isNaN(v))
    const sorted = [...values].sort((a, b) => a - b)
    const mean = values.reduce((a, b) => a + b, 0) / (values.length || 1)
    const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / (values.length || 1)
    usual.push({
      mean,
      sd: Math.sqrt(variance),
      median: median(sorted),
      q25: quantile(sorted, 0.25),
      q75: quantile(sorted, 0.75),
      max: sorted.length ? sorted[sorted.length - 1] : 0,
      years: values.length,
    })
  }

  // score and rank for every month
  const score = years.map(() => new Array(12).fill(NaN))
  const rank = years.map(() => new Array(12).fill(NaN))
  for (let m = 0; m < 12; m++) {
    for (let row = 0; row < years.length; row++) {
      const v = value[row][m]
      if (Number.isNaN(v)) continue
      score[row][m] = usual[m].sd > 0 ? (v - usual[m].mean) / usual[m].sd : 0
      rank[row][m] = 1 + value.filter(other => other[m] > v).length   // 1 = highest
    }
  }

  // yearly totals. A year is "complete" if it has data for all 12 months.
  const yearTotal = value.map(row => row.reduce((a, v) => a + (Number.isNaN(v) ? 0 : v), 0))
  const yearComplete = value.map(row => row.every(v => !Number.isNaN(v)))
  const completeTotals = yearTotal.filter((_, i) => yearComplete[i]).sort((a, b) => a - b)
  const yearEstimated = estimatedShare.map(row => {
    const known = row.filter(v => !Number.isNaN(v))
    return known.length ? known.reduce((a, b) => a + b, 0) / known.length : NaN
  })

  let max = 0
  for (const row of value) for (const v of row) if (v > max) max = v

  return {
    metric, years, value, estimatedShare, usual, score, rank,
    yearTotal, yearComplete, yearEstimated,
    usualYear: median(completeTotals),
    max,
    total: yearTotal.reduce((a, b) => a + b, 0),
  }
}

// ---------------------------------------------------------------------------
// Number formatting
// ---------------------------------------------------------------------------
export function formatNumber(v) {
  if (v >= 100) return Math.round(v).toLocaleString('en-CA')
  if (v >= 10 || Number.isInteger(v)) return String(Math.round(v))
  return v.toFixed(1)
}
export function formatPower(megawatts) {
  if (megawatts >= 1e6) return (megawatts / 1e6).toFixed(1) + ' TW'
  if (megawatts >= 1e3) return (megawatts / 1e3).toFixed(megawatts >= 1e4 ? 0 : 1) + ' GW'
  return Math.round(megawatts) + ' MW'
}
export function formatValue(v, metric) {
  return metric === 'frp' ? formatPower(v) : formatNumber(v)
}
export function formatScore(z) {
  if (Number.isNaN(z)) return 'n/a'
  const rounded = Math.abs(z) < 0.05 ? 0 : z
  return (rounded > 0 ? '+' : rounded < 0 ? '−' : '') + Math.abs(rounded).toFixed(1) + 'σ'
}
function ordinal(n) {
  const tens = n % 100
  if (tens >= 11 && tens <= 13) return n + 'th'
  return n + (['th', 'st', 'nd', 'rd'][n % 10] || 'th')
}
function timesUsual(v, usual) {
  const ratio = v / usual
  if (ratio >= 10) return `${Math.round(ratio)} times`
  if (ratio >= 2) return `${ratio.toFixed(1).replace('.0', '')} times`
  return `${Math.round((ratio - 1) * 100)}% above`
}

// ---------------------------------------------------------------------------
// The verdict: one plain sentence about the selected month (or the whole period)
// ---------------------------------------------------------------------------
// level is one of: 'extreme', 'high', 'normal', 'low', 'none'
export function monthVerdict(table, year, month) {
  const row = year - table.years[0]
  const v = table.value[row][month]
  const u = table.usual[month]
  const z = table.score[row][month]
  const rank = table.rank[row][month]
  const name = MONTHS[month]
  const what = table.metric === 'frp' ? 'fire energy' : 'hotspots'
  const amount = formatValue(v, table.metric) + (table.metric === 'frp' ? '' : v === 1 ? ' hotspot' : ' hotspots')
  const usualText = formatValue(u.median, table.metric)
  const since = table.years[0]

  let level, sentence
  if (u.max === 0) {
    level = 'none'
    sentence = `${name} is outside the fire season here. No ${name} since ${since} has had any hotspots.`
  } else if (v === 0) {
    level = u.median > 0 ? 'low' : 'normal'
    sentence = u.median > 0
      ? `No fire was detected here in ${name} ${year}. A usual ${name} has about ${usualText}${table.metric === 'frp' ? '' : ' hotspots'}.`
      : `No fire was detected here in ${name} ${year}, which is normal for ${name}.`
  } else if (z >= CONFIG.unusualSigma) {
    level = 'extreme'
    const place = rank === 1 ? 'the most active' : `the ${ordinal(rank)} most active`
    const compare = u.median > 0 ? `, ${timesUsual(v, u.median)} the usual ${usualText}` : ''
    sentence = `${name} ${year} was ${place} ${name} on record here: ${amount}${compare}.`
  } else if (z >= 1) {
    level = 'high'
    sentence = `${name} ${year} was busier than usual here: ${amount} against a usual ${usualText}.`
  } else if (u.median > 0 && v < 0.5 * u.median) {
    level = 'low'
    sentence = `${name} ${year} was quieter than usual here: ${amount} against a usual ${usualText}.`
  } else {
    level = 'normal'
    sentence = `${name} ${year} was close to normal here: ${amount} against a usual ${usualText}.`
  }

  return {
    level, sentence, score: z, rank, value: v, usual: u.median, what,
    yearsCompared: u.years,
    estimatedShare: table.estimatedShare[row][month],
  }
}

export function overviewVerdict(table) {
  const first = table.years[0], last = table.years[table.years.length - 1]
  if (table.total === 0) {
    return { level: 'none', sentence: `No hotspots were detected in this area between ${first} and ${last}.`, top: null }
  }
  // peak month of a usual year, and the biggest year
  const by = table.usual.some(u => u.median > 0) ? 'median' : 'mean'
  let peak = 0
  table.usual.forEach((u, m) => { if (u[by] > table.usual[peak][by]) peak = m })
  let big = 0
  table.yearTotal.forEach((t, i) => { if (t > table.yearTotal[big]) big = i })
  // the single most unusual month
  let top = null
  table.years.forEach((y, row) => {
    for (let m = 0; m < 12; m++) {
      const z = table.score[row][m]
      if (!Number.isNaN(z) && (!top || z > top.score)) top = { year: y, month: m, score: z }
    }
  })

  // the month with the most fire overall; it can differ from a usual year's peak when big
  // wildfire years are rare (e.g. steady autumn farm burning, but summer wildfires some years)
  let busiest = 0
  table.usual.forEach((u, m) => { if (u.mean > table.usual[busiest].mean) busiest = m })
  let sentence = busiest !== peak && by === 'median'
    ? `In most years fire here peaks in ${MONTHS[peak]}, but the big fire years peak in ${MONTHS[busiest]}.`
    : `Fire here usually peaks in ${MONTHS[peak]}.`
  if (table.usualYear > 0 && table.years.length > 2) {
    sentence += ` The biggest year since ${first} was ${table.years[big]}, with ${timesUsual(table.yearTotal[big], table.usualYear)} the ${table.metric === 'frp' ? 'fire energy' : 'hotspots'} of a usual year.`
  }
  return { level: 'overview', sentence, top, peakMonth: peak, bigYear: table.years[big] }
}

export const LEVEL_TEXT = {
  extreme: 'Far above normal',
  high: 'Above normal',
  normal: 'Near normal',
  low: 'Below normal',
  none: 'No fire season',
}
