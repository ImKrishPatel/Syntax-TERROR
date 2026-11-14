// Loads hotspots.csv and answers questions like
// "how many hotspots were inside this box in each month?"
//
// The file has one row per grid cell per month. We keep the rows in plain
// arrays (one array per column) because that is fast to loop over.

import { CONFIG } from './config.js'

export const data = {
  cells: [],        // [{ id, lat, lon }]
  months: [],       // ['2001-01', '2001-02', ...] every month from first to last
  firstYear: 0,
  lastYear: 0,
  lastMonthKey: '', // newest month in the file, e.g. '2026-10'
  bounds: null,     // [south, west, north, east] of everything in the file
  // one entry per CSV row:
  rowCell: null,    // index into cells
  rowMonth: null,   // index into months
  rowCount: null,   // hotspot_count
  rowFrp: null,     // frp_mw
  rowEst: null,     // 1 = estimated, 0 = observed
}

export async function loadHotspots() {
  const response = await fetch(CONFIG.files.hotspots)
  if (!response.ok) throw new Error(`Could not load ${CONFIG.files.hotspots}`)
  const text = await response.text()

  const lines = text.split('\n')
  const header = lines[0].trim().split(',')
  const col = {}
  header.forEach((name, i) => { col[name.trim()] = i })
  for (const name of ['cell_id', 'lat', 'lon', 'date', 'hotspot_count', 'frp_mw']) {
    if (!(name in col)) throw new Error(`hotspots.csv is missing the "${name}" column`)
  }

  const n = lines.length - 1
  const rowCell = new Int32Array(n)
  const rowMonthNumber = new Int32Array(n)   // year * 12 + month, fixed up below
  const rowCount = new Float64Array(n)
  const rowFrp = new Float64Array(n)
  const rowEst = new Uint8Array(n)

  const cellIndex = new Map()
  const cells = []
  let rows = 0
  let minMonth = Infinity, maxMonth = -Infinity

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i]
    if (!line) continue
    const parts = line.split(',')
    const id = parts[col.cell_id]

    let c = cellIndex.get(id)
    if (c === undefined) {
      c = cells.length
      cellIndex.set(id, c)
      cells.push({ id, lat: Number(parts[col.lat]), lon: Number(parts[col.lon]) })
    }

    const date = parts[col.date]                      // '2023-08-01'
    const monthNumber = Number(date.slice(0, 4)) * 12 + Number(date.slice(5, 7)) - 1
    if (monthNumber < minMonth) minMonth = monthNumber
    if (monthNumber > maxMonth) maxMonth = monthNumber

    rowCell[rows] = c
    rowMonthNumber[rows] = monthNumber
    rowCount[rows] = Number(parts[col.hotspot_count])
    rowFrp[rows] = Number(parts[col.frp_mw])
    const est = col.is_estimated === undefined ? '' : parts[col.is_estimated].trim().toLowerCase()
    rowEst[rows] = est === 'true' || est === '1' ? 1 : 0
    rows++
  }

  // list every month from January of the first year to the last month in the
  // file, even months with no rows (no rows just means no fire)
  minMonth = Math.floor(minMonth / 12) * 12
  const months = []
  for (let m = minMonth; m <= maxMonth; m++) {
    months.push(`${Math.floor(m / 12)}-${String(m % 12 + 1).padStart(2, '0')}`)
  }
  for (let r = 0; r < rows; r++) rowMonthNumber[r] -= minMonth

  let south = 90, north = -90, west = 180, east = -180
  const half = CONFIG.cellSize / 2
  for (const c of cells) {
    south = Math.min(south, c.lat - half); north = Math.max(north, c.lat + half)
    west = Math.min(west, c.lon - half); east = Math.max(east, c.lon + half)
  }

  Object.assign(data, {
    cells, months,
    firstYear: Math.floor(minMonth / 12),
    lastYear: Math.floor(maxMonth / 12),
    lastMonthKey: months[months.length - 1],
    bounds: [south, west, north, east],
    rowCell: rowCell.subarray(0, rows),
    rowMonth: rowMonthNumber.subarray(0, rows),
    rowCount: rowCount.subarray(0, rows),
    rowFrp: rowFrp.subarray(0, rows),
    rowEst: rowEst.subarray(0, rows),
  })
  return data
}

// Which cells have their centre inside the box? Returns one 0/1 flag per cell.
export function cellsInArea(area) {
  const [south, west, north, east] = area
  const inside = new Uint8Array(data.cells.length)
  data.cells.forEach((c, i) => {
    if (c.lat >= south && c.lat <= north && c.lon >= west && c.lon <= east) inside[i] = 1
  })
  return inside
}

// Totals for every month, for the cells inside the area.
// Returns arrays as long as data.months.
export function monthlyTotals(area) {
  const inside = cellsInArea(area)
  const count = new Float64Array(data.months.length)
  const frp = new Float64Array(data.months.length)
  const estimated = new Float64Array(data.months.length)   // hotspots that are estimates
  for (let r = 0; r < data.rowCell.length; r++) {
    if (!inside[data.rowCell[r]]) continue
    const m = data.rowMonth[r]
    count[m] += data.rowCount[r]
    frp[m] += data.rowFrp[r]
    if (data.rowEst[r]) estimated[m] += data.rowCount[r]
  }
  return { count, frp, estimated }
}

// Totals for every cell (whole file, not just the area) between two months.
// Used to colour the map.
export function cellTotals(firstMonth, lastMonth) {
  const count = new Float64Array(data.cells.length)
  const frp = new Float64Array(data.cells.length)
  for (let r = 0; r < data.rowCell.length; r++) {
    const m = data.rowMonth[r]
    if (m < firstMonth || m > lastMonth) continue
    count[data.rowCell[r]] += data.rowCount[r]
    frp[data.rowCell[r]] += data.rowFrp[r]
  }
  return { count, frp }
}

// The CSV text for "download what I searched for"
export function rowsAsCsv(area, firstMonth, lastMonth) {
  const inside = cellsInArea(area)
  const out = ['cell_id,lat,lon,date,hotspot_count,frp_mw,is_estimated']
  for (let r = 0; r < data.rowCell.length; r++) {
    const m = data.rowMonth[r]
    if (m < firstMonth || m > lastMonth || !inside[data.rowCell[r]]) continue
    const c = data.cells[data.rowCell[r]]
    out.push(`${c.id},${c.lat},${c.lon},${data.months[m]}-01,${data.rowCount[r]},${data.rowFrp[r]},${data.rowEst[r] ? 'true' : 'false'}`)
  }
  return { text: out.join('\n') + '\n', rows: out.length - 1 }
}

export function monthIndex(key) { return data.months.indexOf(key) }

// Small helper for the optional JSON files. Returns null if the file is not there.
export async function loadJson(url) {
  try {
    const response = await fetch(url)
    if (!response.ok) return null
    return await response.json()
  } catch (err) {
    return null
  }
}
