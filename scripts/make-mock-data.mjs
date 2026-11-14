// Makes the demo data files in public/data/ so the app has something to show
// before the real harmonized record is ready.
//
//   node scripts/make-mock-data.mjs
//
// NOTHING in these files is a real measurement. The numbers are random, but the
// big fire seasons are placed where and when real ones happened in western
// Canada (Fort McMurray 2016, BC 2017/2018, the 2023 record year, ...), so the
// demo tells a believable story.
//
// It writes:
//   public/data/hotspots.csv     (format: DATA_FORMAT.md section 1)
//   public/data/forecast.json    (format: DATA_FORMAT.md section 3)
//   public/data/validation.json  (format: DATA_FORMAT.md section 4)
//
// It does NOT write fire_regimes.geojson. That comes from Pearl's pipeline:
//   python -m analysis.fire_regimes.run

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.join(here, '..')
const outDir = path.join(root, 'public', 'data')

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
const CELL = 0.5                 // grid size in degrees
const SOUTH = 48, NORTH = 64     // demo region: western Canada
const WEST = -135, EAST = -95
const FIRST_YEAR = 2001
const LAST_YEAR = 2026
const LAST_MONTH = 10            // data stops at October 2026
const VIIRS_START = 2012         // before this year every row is a model estimate

// ---------------------------------------------------------------------------
// Random numbers that come out the same on every run (so git diffs stay small)
// ---------------------------------------------------------------------------
let seed = 20261114
function rand() {
  seed |= 0; seed = seed + 0x6D2B79F5 | 0
  let t = Math.imul(seed ^ seed >>> 15, 1 | seed)
  t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t
  return ((t ^ t >>> 14) >>> 0) / 4294967296
}
function randNormal() {
  const u = Math.max(rand(), 1e-12), v = rand()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}
// multiplies a value by random "luck": usually near 1, sometimes much bigger
function luck(spread) { return Math.exp(randNormal() * spread - spread * spread / 2) }
function poisson(mean) {
  if (mean <= 0) return 0
  if (mean > 40) return Math.max(0, Math.round(mean + Math.sqrt(mean) * randNormal()))
  const limit = Math.exp(-mean)
  let k = 0, p = 1
  do { k++; p *= rand() } while (p > limit)
  return k - 1
}

// ---------------------------------------------------------------------------
// Land mask: skip cells that are in the ocean
// ---------------------------------------------------------------------------
const require = createRequire(import.meta.url)
const topojson = require(path.join(root, 'vendor', 'topojson-client.min.js'))
const world = JSON.parse(fs.readFileSync(path.join(root, 'public', 'basemap', 'world.json'), 'utf8'))
const countries = topojson.feature(world, world.objects.countries).features
  .filter(f => f.properties.iso === 'CA' || f.properties.iso === 'US')

function insideRing(lon, lat, ring) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j]
    if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside
  }
  return inside
}
function onLand(lat, lon) {
  for (const f of countries) {
    const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates
    for (const poly of polys) {
      if (insideRing(lon, lat, poly[0])) return true
    }
  }
  return false
}

// ---------------------------------------------------------------------------
// Where fire likes to happen. Each "zone" has its own season and intensity.
// ---------------------------------------------------------------------------
function bump(lat, lon, lat0, lon0, sLat, sLon) {
  return Math.exp(-(((lat - lat0) / sLat) ** 2 + ((lon - lon0) / sLon) ** 2) / 2)
}
// smooth random texture so the map does not look like perfect blobs
const waves = Array.from({ length: 6 }, () => ({
  a: rand() * 2.2 + 0.5, b: rand() * 2.2 + 0.5, p: rand() * 6.28, q: rand() * 6.28,
}))
function texture(lat, lon) {
  let s = 0
  for (const w of waves) s += Math.sin(lat * w.a + w.p) * Math.cos(lon * w.b + w.q)
  return Math.exp(s * 0.38)
}

//               J  F  M    A    M    J    J    A    S    O    N  D
const SEASON = {
  boreal: [0, 0, 0, .02, .15, .22, .29, .22, .08, .02, 0, 0],   // northern forest: summer wildfire
  interior: [0, 0, 0, .01, .03, .08, .30, .42, .14, .02, 0, 0], // BC interior: late-summer wildfire
  crop: [0, 0, .03, .25, .28, .02, .01, .03, .10, .24, .04, 0], // prairies: spring + autumn burning
}
const FRP_PER_HOTSPOT = { boreal: 14, interior: 17, crop: 4 }   // megawatts, rough averages

function zoneWeights(lat, lon) {
  // the boreal belt runs from the NWT down through northern AB, SK and MB
  const beltLat = 61 - 0.33 * (lon + 120)
  const boreal = Math.exp(-(((lat - beltLat) / 2.4) ** 2) / 2) * (lon > -126 ? 1 : 0.35)
  const interior = bump(lat, lon, 52, -122.5, 2.6, 2.2)
  const crop = (lat < 54 ? bump(lat, lon, 51, -106, 1.7, 7) : 0) * (lon > -114.5 ? 1 : 0.1)
  return { boreal, interior, crop }
}

// year-to-year swings per zone (dry year / wet year), with a slow upward trend
const yearFactor = {}
for (const zone of Object.keys(SEASON)) {
  yearFactor[zone] = {}
  for (let y = FIRST_YEAR; y <= LAST_YEAR; y++) {
    const trend = zone === 'crop' ? 1 : 1 + 0.018 * (y - FIRST_YEAR)
    yearFactor[zone][y] = luck(zone === 'crop' ? 0.2 : 0.55) * trend
  }
}

// Big fire seasons, placed where real ones happened. size = rough hotspot total.
const EVENTS = [
  { name: 'Okanagan / Kamloops', y: 2003, months: { 8: .7, 9: .3 }, lat: 50.3, lon: -119.8, sLat: .8, sLon: .9, size: 9000 },
  { name: 'Northern Saskatchewan', y: 2010, months: { 5: .3, 6: .4, 7: .3 }, lat: 56.5, lon: -105.5, sLat: 1.2, sLon: 2, size: 12000 },
  { name: 'Slave Lake', y: 2011, months: { 5: .85, 6: .15 }, lat: 55.5, lon: -114.5, sLat: .7, sLon: 1.1, size: 14000 },
  { name: 'Richardson fire', y: 2011, months: { 5: .5, 6: .4, 7: .1 }, lat: 57.9, lon: -111.3, sLat: .7, sLon: .7, size: 16000 },
  { name: 'NWT 2014', y: 2014, months: { 6: .2, 7: .45, 8: .35 }, lat: 61.6, lon: -116.5, sLat: 1.1, sLon: 3, size: 60000 },
  { name: 'BC north 2014', y: 2014, months: { 7: .5, 8: .5 }, lat: 55, lon: -124.5, sLat: 1.4, sLon: 1.4, size: 14000 },
  { name: 'La Ronge', y: 2015, months: { 6: .45, 7: .55 }, lat: 55.8, lon: -105.5, sLat: 1.1, sLon: 2, size: 42000 },
  { name: 'Northern Alberta 2015', y: 2015, months: { 5: .3, 6: .4, 7: .3 }, lat: 57.5, lon: -115, sLat: 1.2, sLon: 2, size: 15000 },
  { name: 'Fort McMurray', y: 2016, months: { 5: .8, 6: .2 }, lat: 56.6, lon: -110.9, sLat: .6, sLon: .9, size: 38000 },
  { name: 'BC interior 2017', y: 2017, months: { 7: .45, 8: .45, 9: .1 }, lat: 52, lon: -122.8, sLat: 1.3, sLon: 1.2, size: 70000 },
  { name: 'BC 2018', y: 2018, months: { 8: .85, 9: .15 }, lat: 53.9, lon: -125.3, sLat: 1.3, sLon: 1.6, size: 65000 },
  { name: 'Chuckegg Creek', y: 2019, months: { 5: .6, 6: .4 }, lat: 58.3, lon: -117.3, sLat: .7, sLon: 1, size: 32000 },
  { name: 'Lytton / BC 2021', y: 2021, months: { 6: .2, 7: .55, 8: .25 }, lat: 50.8, lon: -120.6, sLat: 1.1, sLon: 1.3, size: 48000 },
  { name: 'Northern SK 2021', y: 2021, months: { 7: .7, 8: .3 }, lat: 56.5, lon: -104.5, sLat: 1, sLon: 2, size: 20000 },
  { name: 'Alberta May 2023', y: 2023, months: { 5: .6, 6: .3, 7: .1 }, lat: 55.8, lon: -116.3, sLat: 1.5, sLon: 2.4, size: 95000 },
  { name: 'NWT 2023', y: 2023, months: { 7: .2, 8: .5, 9: .3 }, lat: 61, lon: -117, sLat: 1.2, sLon: 3.2, size: 110000 },
  { name: 'BC 2023 (Donnie Creek)', y: 2023, months: { 5: .15, 6: .3, 7: .3, 8: .25 }, lat: 57.3, lon: -122.3, sLat: 1.1, sLon: 1.3, size: 80000 },
  { name: 'BC interior 2023', y: 2023, months: { 7: .3, 8: .6, 9: .1 }, lat: 51, lon: -119.6, sLat: 1, sLon: 1.1, size: 45000 },
  { name: 'Jasper', y: 2024, months: { 7: .75, 8: .25 }, lat: 52.8, lon: -118, sLat: .45, sLon: .5, size: 13000 },
  { name: 'Northern Alberta 2024', y: 2024, months: { 5: .2, 7: .5, 8: .3 }, lat: 58, lon: -113.5, sLat: 1.2, sLon: 2, size: 34000 },
  { name: 'SK / MB 2025', y: 2025, months: { 5: .45, 6: .35, 7: .2 }, lat: 55.2, lon: -102.5, sLat: 1.2, sLon: 2.6, size: 105000 },
  { name: 'Northern Alberta 2025', y: 2025, months: { 5: .4, 6: .4, 7: .2 }, lat: 56.5, lon: -113, sLat: 1, sLon: 1.6, size: 26000 },
  { name: 'Peace country 2026', y: 2026, months: { 6: .4, 7: .45, 8: .15 }, lat: 56.8, lon: -118.5, sLat: 1, sLon: 1.5, size: 30000 },
]

// ---------------------------------------------------------------------------
// Build the grid
// ---------------------------------------------------------------------------
const cells = []
for (let lat = SOUTH + CELL / 2; lat < NORTH; lat += CELL) {
  for (let lon = WEST + CELL / 2; lon < EAST; lon += CELL) {
    if (!onLand(lat, lon)) continue
    const w = zoneWeights(lat, lon)
    const tex = texture(lat, lon)
    cells.push({
      id: `${lat}_${lon}`, lat, lon,
      boreal: w.boreal * tex, interior: w.interior * tex, crop: w.crop * (0.6 + 0.4 * tex),
    })
  }
}

// pre-compute how each event's hotspots are shared out between cells
for (const ev of EVENTS) {
  let total = 0
  ev.share = cells.map(c => {
    const s = bump(c.lat, c.lon, ev.lat, ev.lon, ev.sLat, ev.sLon) * luck(0.7)
    total += s
    return s
  })
  ev.share = ev.share.map(s => s / total)
}

// ---------------------------------------------------------------------------
// Hotspots, month by month
// ---------------------------------------------------------------------------
const BASE = { boreal: 95, interior: 60, crop: 16 }   // typical hotspots per cell per year
const rows = ['cell_id,lat,lon,date,hotspot_count,frp_mw,is_estimated']
let totalHotspots = 0

for (let y = FIRST_YEAR; y <= LAST_YEAR; y++) {
  for (let m = 1; m <= 12; m++) {
    if (y === LAST_YEAR && m > LAST_MONTH) break
    const date = `${y}-${String(m).padStart(2, '0')}-01`
    const estimated = y < VIIRS_START
    const eventsNow = EVENTS.filter(ev => ev.y === y && ev.months[m])

    cells.forEach((c, i) => {
      let count = 0, frp = 0
      for (const zone of ['boreal', 'interior', 'crop']) {
        const mean = BASE[zone] * c[zone] * SEASON[zone][m - 1] * yearFactor[zone][y]
        if (mean < 0.3) continue      // too little fire to bother with
        // wildfire is "bursty": most cell-months get nothing, a few get a lot
        if (rand() > 0.4) continue
        const n = poisson(mean / 0.4 * luck(zone === 'crop' ? 0.5 : 1.25))
        count += n
        frp += n * FRP_PER_HOTSPOT[zone] * luck(0.35)
      }
      for (const ev of eventsNow) {
        const n = poisson(ev.size * ev.months[m] * ev.share[i])
        count += n
        frp += n * 24 * luck(0.3)       // big fires burn hotter per hotspot
      }
      if (count === 0) return
      totalHotspots += count
      rows.push(`${c.id},${c.lat},${c.lon},${date},${count},${frp.toFixed(1)},${estimated}`)
    })
  }
}

fs.mkdirSync(outDir, { recursive: true })
fs.writeFileSync(path.join(outDir, 'hotspots.csv'), rows.join('\n') + '\n')
console.log(`hotspots.csv: ${rows.length - 1} rows, ${cells.length} cells, ${totalHotspots} hotspots`)

// ---------------------------------------------------------------------------
// Forecast sample (Snavya's slot): expected vs actual hotspots per day
// ---------------------------------------------------------------------------
const days = []
const start = new Date(Date.UTC(2026, 6, 16))      // 16 July 2026
const lastActual = 29                              // actual counts exist for the first 30 days
let level = 620
for (let d = 0; d < 60; d++) {
  const date = new Date(start.getTime() + d * 86400000).toISOString().slice(0, 10)
  level = level * (0.985 + 0.05 * randNormal()) + (d > 12 && d < 20 ? 55 : 0)
  const expected = Math.max(40, level)
  const spread = expected * (0.22 + 0.006 * d)     // the further ahead, the less sure
  const day = {
    date,
    expected: Math.round(expected),
    low: Math.round(Math.max(0, expected - spread)),
    high: Math.round(expected + spread),
  }
  if (d <= lastActual) day.actual = Math.round(Math.max(0, expected * luck(0.22)))
  days.push(day)
}
fs.writeFileSync(path.join(outDir, 'forecast.json'), JSON.stringify({
  is_demo: true,
  region: 'Whole demo region',
  made_on: days[lastActual].date,
  unit: 'hotspots per day',
  days,
}, null, 1))
console.log('forecast.json: 60 days')

// ---------------------------------------------------------------------------
// Validation sample (Aashana + Shriya's slot): how good is the harmonization?
// ---------------------------------------------------------------------------
fs.writeFileSync(path.join(outDir, 'validation.json'), JSON.stringify({
  is_demo: true,
  overlap_period: '2012 to 2024',
  note: 'Model estimates compared with real VIIRS in years where both sensors were flying.',
  metrics: [
    { label: 'Monthly hotspot counts', value: '0.93', unit: 'R²', detail: 'estimated vs real VIIRS, per cell' },
    { label: 'Fire energy (FRP)', value: '11', unit: '% error', detail: 'median error after calibration' },
    { label: 'Fire or no fire', value: '0.88', unit: 'F1', detail: 'per 375 m pixel, held-out year' },
  ],
}, null, 1))
console.log('validation.json written')
