// Shared selection state: the selected AREA and DATE RANGE.
//
// The URL is the single source of truth, so every link is shareable:
//   #/?bbox=-120,52,-110,60&from=2023-05-01&to=2023-09-30
//   #/?poly=55.1,-118.2;56.3,-114.0;53.9,-113.5&from=...&to=...
//
// Any component can read or change the selection:
//   const { area, bounds, from, to, setArea, setDates } = useSelection()
//
// area   = { type: 'bbox', bounds: [west, south, east, north] }
//        | { type: 'polygon', points: [[lat, lon], ...] }
// bounds = [west, south, east, north] of the area (always present)
// from / to = 'YYYY-MM-DD' strings (inclusive)

import { useMemo, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

export const DEFAULT_BBOX = [-120, 52, -110, 60] // northern Alberta
export const DEFAULT_FROM = '2023-05-01'
export const DEFAULT_TO = '2023-09-30'

const round = (n) => Math.round(n * 1000) / 1000
const isDate = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || '')

function parseBbox(text) {
  const n = (text || '').split(',').map(Number)
  if (n.length !== 4 || n.some((v) => !Number.isFinite(v))) return null
  const [w, s, e, nth] = n
  if (w >= e || s >= nth || s < -90 || nth > 90) return null
  return n
}

function parsePoly(text) {
  const points = (text || '').split(';').map((p) => p.split(',').map(Number))
  const ok = points.length >= 3 && points.every((p) => p.length === 2 && p.every(Number.isFinite))
  return ok ? points : null
}

// [west, south, east, north] for any area
export function areaBounds(area) {
  if (area.type === 'bbox') return area.bounds
  const lats = area.points.map((p) => p[0])
  const lons = area.points.map((p) => p[1])
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)]
}

// Is the point (lat, lon) inside the area?
export function areaContains(area, lat, lon) {
  const [w, s, e, n] = areaBounds(area)
  if (lon < w || lon > e || lat < s || lat > n) return false
  if (area.type === 'bbox') return true
  // ray casting for polygons
  let inside = false
  const pts = area.points
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [yi, xi] = pts[i]
    const [yj, xj] = pts[j]
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

export function useSelection() {
  const [params, setParams] = useSearchParams()
  const bboxText = params.get('bbox')
  const polyText = params.get('poly')
  const fromText = params.get('from')
  const toText = params.get('to')

  const area = useMemo(() => {
    const points = parsePoly(polyText)
    if (points) return { type: 'polygon', points }
    return { type: 'bbox', bounds: parseBbox(bboxText) || DEFAULT_BBOX }
  }, [bboxText, polyText])

  const bounds = useMemo(() => areaBounds(area), [area])
  const from = isDate(fromText) ? fromText : DEFAULT_FROM
  const to = isDate(toText) ? toText : DEFAULT_TO

  const setArea = useCallback(
    (next) => {
      setParams((prev) => {
        const p = new URLSearchParams(prev)
        p.delete('bbox')
        p.delete('poly')
        if (next.type === 'polygon') {
          p.set('poly', next.points.map(([lat, lon]) => `${round(lat)},${round(lon)}`).join(';'))
        } else {
          p.set('bbox', next.bounds.map(round).join(','))
        }
        return p
      })
    },
    [setParams]
  )

  const setDates = useCallback(
    (nextFrom, nextTo) => {
      setParams((prev) => {
        const p = new URLSearchParams(prev)
        p.set('from', nextFrom)
        p.set('to', nextTo)
        return p
      })
    },
    [setParams]
  )

  return { area, bounds, from, to, setArea, setDates }
}
