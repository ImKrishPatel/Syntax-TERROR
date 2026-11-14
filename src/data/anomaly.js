// Anomaly stats for the verdict card.
//
// For the selected area we add up hotspots per month, pick the most intense
// month inside the selected dates, and compare it with:
//   - the same calendar month in every year on record  -> z-score (sigma)
//   - every month on record                             -> rank
//
// If Shriya's method differs, this is the only function to replace. It must
// return the same fields (or null when there are no hotspots).

import { filterRecords } from './hotspots.js'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function computeAnomaly(records, area, from, to) {
  if (records.length === 0) return null

  // years covered by the whole dataset
  let firstYear = Infinity
  let lastYear = -Infinity
  for (const r of records) {
    const year = Number(r.date.slice(0, 4))
    if (year < firstYear) firstYear = year
    if (year > lastYear) lastYear = year
  }

  // hotspots per month in the area, keyed by 'YYYY-MM'
  const monthly = new Map()
  const estimatedMonths = new Set()
  for (const r of filterRecords(records, area)) {
    const key = r.date.slice(0, 7)
    monthly.set(key, (monthly.get(key) || 0) + r.hotspot_count)
    if (r.is_estimated) estimatedMonths.add(key)
  }

  // most intense month inside the selected dates
  let peakKey = null
  for (const [key, value] of monthly) {
    if (key < from.slice(0, 7) || key > to.slice(0, 7)) continue
    if (peakKey === null || value > monthly.get(peakKey)) peakKey = key
  }
  if (peakKey === null) return null

  const value = monthly.get(peakKey)
  const [peakYear, peakMonth] = peakKey.split('-').map(Number)

  // same calendar month in every year (months with no rows count as 0)
  const sameMonth = []
  for (let year = firstYear; year <= lastYear; year++) {
    sameMonth.push(monthly.get(`${year}-${String(peakMonth).padStart(2, '0')}`) || 0)
  }
  const mean = sameMonth.reduce((a, b) => a + b, 0) / sameMonth.length
  const variance = sameMonth.reduce((a, b) => a + (b - mean) ** 2, 0) / sameMonth.length
  const std = Math.sqrt(variance)

  return {
    monthLabel: `${MONTHS[peakMonth - 1]} ${peakYear}`, // 'Aug 2023'
    monthName: MONTHS[peakMonth - 1], // 'Aug'
    hotspots: value, // hotspots in the area that month
    typical: mean, // average for that calendar month
    zScore: std > 0 ? (value - mean) / std : 0, // sigma above/below typical
    rank: [...monthly.values()].filter((v) => v > value).length + 1, // 1 = most intense on record
    firstYear,
    lastYear,
    isEstimated: estimatedMonths.has(peakKey), // true = model estimate, not VIIRS
  }
}
