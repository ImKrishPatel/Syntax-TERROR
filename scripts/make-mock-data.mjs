// Generates MOCK data in public/data/ so the app can be built before the real
// data is ready. Run with:  node scripts/make-mock-data.mjs
//
// Output matches DATA_FORMAT.md. Replace the files with real ones when ready.

import { writeFileSync, mkdirSync } from 'node:fs'

// deterministic random numbers so the mock files don't change on every run
let seed = 42
const rand = () => {
  seed = (seed * 1664525 + 1013904223) % 4294967296
  return seed / 4294967296
}
const randNormal = () => Math.sqrt(-2 * Math.log(rand() + 1e-9)) * Math.cos(2 * Math.PI * rand())

// ---- grid: 0.5 degree cells over western Canada ----
const CELL = 0.5
const WEST = -125, EAST = -100, SOUTH = 49, NORTH = 62
const FIRST_YEAR = 2001, LAST_YEAR = 2024
const VIIRS_START_YEAR = 2012 // before this, values are model estimates

// fire season strength per month (index 0 = January)
const SEASON = [0, 0, 0, 0.15, 0.7, 0.8, 1, 0.9, 0.4, 0.1, 0, 0]
// some years are much worse than others
const BIG_YEARS = { 2003: 1.6, 2011: 1.5, 2014: 1.7, 2015: 1.8, 2016: 1.6, 2019: 2.2, 2021: 1.9, 2023: 4.5 }

// a few "fire-prone centres" make the map look realistic instead of uniform noise
const CENTRES = [
  { lat: 58.5, lon: -115, strength: 1.0, spread: 3.5 },
  { lat: 56.5, lon: -111.5, strength: 0.9, spread: 2.5 },
  { lat: 60.5, lon: -118, strength: 0.8, spread: 3 },
  { lat: 53.5, lon: -122.5, strength: 0.7, spread: 2.5 },
  { lat: 56, lon: -104, strength: 0.8, spread: 3 },
  { lat: 50.5, lon: -119.5, strength: 0.6, spread: 1.8 },
]
const proneness = (lat, lon) =>
  CENTRES.reduce((sum, c) => {
    const d2 = (lat - c.lat) ** 2 + ((lon - c.lon) * 0.6) ** 2
    return sum + c.strength * Math.exp(-d2 / (2 * c.spread ** 2))
  }, 0.03)

const rows = ['cell_id,lat,lon,date,hotspot_count,frp_mw,is_estimated']

for (let year = FIRST_YEAR; year <= LAST_YEAR; year++) {
  const trend = 1 + (year - FIRST_YEAR) * 0.02
  const yearFactor = (BIG_YEARS[year] || 0.6 + rand() * 0.6) * trend
  for (let month = 0; month < 12; month++) {
    if (SEASON[month] === 0) continue
    const monthFactor = SEASON[month] * (0.7 + rand() * 0.6)
    const date = `${year}-${String(month + 1).padStart(2, '0')}-01`
    for (let lat = SOUTH + CELL / 2; lat < NORTH; lat += CELL) {
      for (let lon = WEST + CELL / 2; lon < EAST; lon += CELL) {
        const expected = 40 * proneness(lat, lon) * monthFactor * yearFactor
        // fires are patchy: most cells have none in a given month
        if (rand() > Math.min(0.8, 0.02 + expected / 60)) continue
        const count = Math.round(expected * Math.exp(randNormal() * 0.9))
        if (count < 1) continue
        const frpPerHotspot = 15 + rand() * 45 // MW
        const frp = Math.round(count * frpPerHotspot * 10) / 10
        const isEstimated = year < VIIRS_START_YEAR
        rows.push(`${lat}_${lon},${lat},${lon},${date},${count},${frp},${isEstimated}`)
      }
    }
  }
}

mkdirSync('public/data', { recursive: true })
writeFileSync('public/data/hotspots.csv', rows.join('\n') + '\n')
console.log(`hotspots.csv: ${rows.length - 1} rows`)

// ---- fire regime zones (mock): simple boxes with a regime label ----
const box = (w, s, e, n) => [[[w, s], [e, s], [e, n], [w, n], [w, s]]]
const regimes = [
  { id: 1, name: 'Frequent, low-intensity', color: '#f6c85f', coords: box(-125, 49, -114, 53) },
  { id: 2, name: 'Mixed severity', color: '#f28e2b', coords: box(-114, 49, -100, 54) },
  { id: 3, name: 'Infrequent, high-intensity (boreal)', color: '#e15759', coords: box(-125, 53, -110, 62) },
  { id: 4, name: 'Shifting: longer seasons', color: '#b07aa1', coords: box(-110, 54, -100, 62) },
]
const geojson = {
  type: 'FeatureCollection',
  features: regimes.map((r) => ({
    type: 'Feature',
    properties: { regime_id: r.id, regime_name: r.name, color: r.color },
    geometry: { type: 'Polygon', coordinates: r.coords },
  })),
}
writeFileSync('public/data/fire_regimes.geojson', JSON.stringify(geojson, null, 2) + '\n')
console.log(`fire_regimes.geojson: ${regimes.length} zones`)
