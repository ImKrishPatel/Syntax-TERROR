// The map: a simple basemap we draw ourselves (no map tiles needed, so it
// works offline), the hotspot grid, the selected area, and the regime layer.
//
// Uses Leaflet (window.L) and topojson-client (window.topojson) from /vendor.

import { CONFIG, MONTHS_SHORT } from './config.js'
import { data } from './data.js'
import { FIRE_RAMP, fireBreaks, fireColor } from './colors.js'
import { formatNumber, formatPower } from './stats.js'

// Map colours. Keep these in step with the variables at the top of style.css.
const LAND = '#E6EADF'
const WATER = '#C3D6DD'
const WATER_LINE = '#9DBAC5'
const PAPER = '#EEF1EB'
const MAGENTA = '#B4136B'
const INK = '#17231D'

export function createMap(element, legendElement, { onAreaChange, onRegimeClick }) {
  const L = window.L
  const half = CONFIG.cellSize / 2

  const map = L.map(element, {
    zoomSnap: 0.25, zoomDelta: 0.5, wheelPxPerZoomLevel: 90,
    minZoom: 2, maxZoom: 8,
    maxBounds: [[-80, -260], [86, 260]],
    zoomControl: false, attributionControl: false,
    boxZoom: false,   // Shift + drag draws a new area instead
  })
  L.control.zoom({ position: 'topright' }).addTo(map)
  L.control.attribution({ prefix: false, position: 'bottomright' }).addTo(map)

  // Layers are stacked in "panes". Each pane gets its own canvas.
  function makePane(name, zIndex, clickable) {
    const pane = map.createPane(name)
    pane.style.zIndex = zIndex
    if (!clickable) pane.style.pointerEvents = 'none'
    return L.canvas({ pane: name, padding: 0.4 })
  }
  map.createPane('imagery').style.zIndex = 150          // satellite photos, under everything
  const labelPane = map.createPane('imageryLabels')     // satellite place names, over the cells
  labelPane.style.zIndex = 360; labelPane.style.pointerEvents = 'none'
  const baseCanvas = makePane('base', 200, false)
  const cellCanvas = makePane('cells', 300, true)
  const lineCanvas = makePane('lines', 350, false)   // borders and water are drawn over the cells
  const areaCanvas = makePane('area', 400, false)

  // -------------------------------------------------------------------------
  // Basemap
  // -------------------------------------------------------------------------
  // -------------------------------------------------------------------------
  // Satellite basemap (Esri World Imagery: free, no key, attribution required)
  // -------------------------------------------------------------------------
  const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/'
  const imagery = L.tileLayer(ESRI + 'World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    pane: 'imagery', maxNativeZoom: 18,
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community',
  })
  const imageryLabels = L.tileLayer(ESRI + 'Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', {
    pane: 'imageryLabels', maxNativeZoom: 18,
  })
  // "Map": Esri's dark grey canvas, so fire colours stand out
  const darkBase = L.tileLayer(ESRI + 'Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
    pane: 'imagery', maxNativeZoom: 16, attribution: 'Map © Esri, HERE, Garmin, OpenStreetMap contributors',
  })
  const darkLabels = L.tileLayer(ESRI + 'Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
    pane: 'imageryLabels', maxNativeZoom: 16,
  })
  const vectorBase = L.layerGroup()   // Natural Earth outlines; only used if the tiles can't load
  let basemap = 'satellite'

  function setBasemap(name) {
    basemap = name
    const satellite = name === 'satellite'
    element.classList.toggle('is-satellite', satellite)
    element.classList.toggle('is-dark', !satellite)
    for (const l of [imagery, imageryLabels, darkBase, darkLabels]) map.removeLayer(l)
    if (satellite) { imagery.addTo(map); imageryLabels.addTo(map) } else { darkBase.addTo(map); darkLabels.addTo(map) }
    if (regimeLayer) regimeLayer.setStyle(regimeStyle)
  }

  let places = []
  async function loadBasemap() {
    const world = await (await fetch(CONFIG.files.basemap)).json()
    const layer = name => window.topojson.feature(world, world.objects[name])
    const plain = { renderer: baseCanvas, interactive: false }

    const over = { renderer: lineCanvas, interactive: false }

    // land goes under the hotspot cells; water and borders go on top of them
    L.geoJSON(layer('countries'), { ...plain, style: { fillColor: LAND, fillOpacity: 1, stroke: false } }).addTo(vectorBase)
    L.geoJSON(layer('rivers2'), { ...over, style: { color: WATER_LINE, weight: 0.9, fill: false } }).addTo(vectorBase)
    L.geoJSON(layer('lakes'), { ...over, style: { fillColor: WATER, fillOpacity: 1, color: WATER_LINE, weight: 0.6 } }).addTo(vectorBase)
    L.geoJSON(layer('states'), { ...over, style: { color: INK, opacity: 0.5, weight: 0.8, dashArray: '5 3', fill: false } }).addTo(vectorBase)
    L.geoJSON(layer('countries'), { ...over, style: { fill: false, color: INK, opacity: 0.55, weight: 0.9 } }).addTo(vectorBase)

    places = layer('places').features.map(f => ({
      name: f.properties.name, rank: f.properties.scalerank,
      lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0],
    }))
    for (const [name, lat, lon] of CONFIG.extraPlaces) places.push({ name, rank: 6, lat, lon })
    drawPlaces()
  }

  // town names: show more of them as you zoom in
  const placeLayer = L.layerGroup()   // not shown: the map tiles carry place names. Kept for search.
  function drawPlaces() {
    placeLayer.clearLayers()
    const zoom = map.getZoom()
    const maxRank = zoom < 3.5 ? 1 : zoom < 4.75 ? 3 : zoom < 5.75 ? 4 : 9
    const view = map.getBounds().pad(0.1)
    for (const p of places) {
      if (p.rank > maxRank || !view.contains([p.lat, p.lon])) continue
      L.marker([p.lat, p.lon], {
        interactive: false, keyboard: false,
        icon: L.divIcon({ className: 'place', html: `<i></i><span>${p.name}</span>`, iconSize: null }),
      }).addTo(placeLayer)
    }
  }

  // latitude / longitude lines with labels along the edge, like a printed map sheet
  const gridLines = L.layerGroup().addTo(map)
  const gridLabels = L.DomUtil.create('div', 'graticule-labels', element)
  function drawGraticule() {
    const step = map.getZoom() >= 5.5 ? 2 : map.getZoom() >= 3.75 ? 5 : 10
    gridLines.clearLayers()
    const style = { renderer: baseCanvas, interactive: false, color: INK, opacity: 0.13, weight: 0.6 }
    for (let lon = -180; lon <= 180; lon += step) L.polyline([[-85, lon], [85, lon]], style).addTo(gridLines)
    for (let lat = -80; lat <= 80; lat += step) L.polyline([[lat, -180], [lat, 180]], style).addTo(gridLines)
    labelGraticule()
  }
  function labelGraticule() {
    const step = map.getZoom() >= 5.5 ? 2 : map.getZoom() >= 3.75 ? 5 : 10
    const b = map.getBounds(), size = map.getSize(), centre = map.getCenter()
    let html = ''
    for (let lon = Math.ceil(b.getWest() / step) * step; lon <= b.getEast(); lon += step) {
      if (lon < -180 || lon > 180) continue
      const x = map.latLngToContainerPoint([centre.lat, lon]).x
      if (x < 40 || x > size.x - 40) continue
      html += `<span style="left:${x}px;bottom:4px">${Math.abs(lon)}°${lon < 0 ? 'W' : lon > 0 ? 'E' : ''}</span>`
    }
    for (let lat = Math.ceil(b.getSouth() / step) * step; lat <= b.getNorth(); lat += step) {
      const y = map.latLngToContainerPoint([lat, centre.lng]).y
      if (y < 30 || y > size.y - 30) continue
      html += `<span style="left:6px;top:${y}px" class="side">${Math.abs(lat)}°${lat < 0 ? 'S' : lat > 0 ? 'N' : ''}</span>`
    }
    gridLabels.innerHTML = html
  }
  map.on('zoomend', () => { drawGraticule(); drawPlaces() })
  map.on('moveend', drawPlaces)
  map.on('move', labelGraticule)

  // -------------------------------------------------------------------------
  // Hotspot grid: one square per cell, recoloured whenever the selection changes
  // -------------------------------------------------------------------------
  const cellGroup = L.featureGroup()
  let cellSquares = []
  let cellValues = null, cellMetric = 'count', cellPeriod = ''

  function buildCells() {
    cellSquares = data.cells.map((c, i) => {
      const square = L.rectangle([[c.lat - half, c.lon - half], [c.lat + half, c.lon + half]], {
        renderer: cellCanvas, color: LAND, weight: 1, fillOpacity: 0, opacity: 0,
      })
      square.cellNumber = i
      return square.addTo(cellGroup)
    })
    cellGroup.bindTooltip(layer => {
      const c = data.cells[layer.cellNumber]
      const v = cellValues ? cellValues[cellMetric][layer.cellNumber] : 0
      const where = `${Math.abs(c.lat)}°${c.lat < 0 ? 'S' : 'N'}, ${Math.abs(c.lon)}°${c.lon < 0 ? 'W' : 'E'}`
      if (!v) return `<b>No hotspots</b><br>${cellPeriod}<br><span class="dim">${where}</span>`
      return `<b>${formatNumber(cellValues.count[layer.cellNumber])} hotspots</b><br>` +
        `${formatPower(cellValues.frp[layer.cellNumber])} fire energy<br>${cellPeriod}<br><span class="dim">${where}</span>`
    }, { sticky: true, className: 'map-tip', direction: 'top', offset: [0, -6] })

    // dashed outline around everything the data file covers
    const [s, w, n, e] = data.bounds
    L.rectangle([[s, w], [n, e]], {
      renderer: lineCanvas, interactive: false, fill: false, color: INK, opacity: 0.7, weight: 1, dashArray: '2 5',
    }).addTo(map)
    L.marker([n, w], {
      interactive: false, keyboard: false,
      icon: L.divIcon({
        className: 'coverage-label', iconSize: null,
        html: `<span>${CONFIG.isDemoData ? 'Demo data covers this box' : 'Data coverage'}</span>`,
      }),
    }).addTo(map)
  }

  // values: { count, frp } with one number per cell. period: text for the tooltip.
  // Hotspots are drawn as a soft glow (Leaflet.heat) instead of hard squares.
  // The squares stay, invisible, so hovering still shows each cell's numbers.
  const heat = L.heatLayer([], {
    minOpacity: 0.25, maxZoom: 1, max: 1,   // maxZoom below minZoom: intensity never rescales with zoom
    gradient: { 0.15: FIRE_RAMP[0], 0.35: FIRE_RAMP[1], 0.55: FIRE_RAMP[2], 0.72: FIRE_RAMP[3], 0.88: FIRE_RAMP[4], 1: FIRE_RAMP[5] },
  })
  const heatPane = map.createPane('heat'); heatPane.style.zIndex = 250; heatPane.style.pointerEvents = 'none'
  function sizeHeat() {
    // radius follows the size of one grid cell on screen
    if (!map.hasLayer(heat)) return
    const a = map.latLngToContainerPoint([0, 0]), b = map.latLngToContainerPoint([0, CONFIG.cellSize])
    const px = Math.max(6, Math.abs(b.x - a.x))
    heat.setOptions({ radius: px * 0.62, blur: px * 0.42 })
  }
  map.on('zoomend', sizeHeat)
  // Leaflet.heat always puts its canvas in the overlay pane; move it under the area box and back
  let heatPoints = []
  function addHeat() {
    if (map.hasLayer(heat)) return
    heat.addTo(map); heatPane.appendChild(heat._canvas); sizeHeat()
    heat.setLatLngs(heatPoints)
  }
  function removeHeat() {
    if (!map.hasLayer(heat)) return
    if (heat._frame) { L.Util.cancelAnimFrame(heat._frame); heat._frame = null }
    map.getPanes().overlayPane.appendChild(heat._canvas)
    map.removeLayer(heat)
  }

  function setCells(values, metric, period) {
    cellValues = values; cellMetric = metric; cellPeriod = period
    const v = values[metric]
    const breaks = fireBreaks(v)
    const points = []
    v.forEach((value, i) => {
      if (!value) return
      let step = 0
      while (step < breaks.length - 1 && value >= breaks[step + 1]) step++
      const c = data.cells[i]
      points.push([c.lat, c.lon, (step + 1) / breaks.length])
    })
    cellSquares.forEach(square => square.setStyle({ fillOpacity: 0, opacity: 0 }))
    heatPoints = points
    if (showing === 'hotspots') { addHeat(); sizeHeat(); heat.setLatLngs(points) }
    hotspotLegend = { breaks, metric, period }
    drawLegend()
  }

  // -------------------------------------------------------------------------
  // Fire regime layer (Pearl's fire_regimes.geojson)
  // -------------------------------------------------------------------------
  let regimeLayer = null, regimeList = [], showing = 'hotspots', hotspotLegend = null

  function setRegimes(geojson) {
    const seen = new Map()
    for (const f of geojson.features) {
      const p = f.properties
      if (!seen.has(p.regime_id)) seen.set(p.regime_id, { name: p.regime_name, color: p.color || '#999' })
    }
    regimeLayer = L.geoJSON(geojson, { renderer: cellCanvas, style: regimeStyle })
    regimeLayer.on('click', e => { if (onRegimeClick) onRegimeClick(e.layer.feature.properties, e.latlng) })
    regimeLayer.bindTooltip(layer => {
      const p = layer.feature.properties
      let html = `<b>${p.regime_name}</b>`
      if (p.hotspots_per_year !== undefined) html += `<br>About ${formatNumber(p.hotspots_per_year)} hotspots a year`
      if (p.peak_month) html += `<br>Peaks in ${MONTHS_SHORT[p.peak_month - 1]}`
      if (p.shifted) html += `<br>Changed from "${p.shift_from}" to "${p.shift_to}"`
      return html
    }, { sticky: true, className: 'map-tip', direction: 'top', offset: [0, -6] })
    regimeList = [...seen.entries()].sort((a, b) => a[0] - b[0]).map(([, r]) => r)
  }

  function regimeStyle(f) {
    const p = f.properties
    return {
      fillColor: p.color || '#999', fillOpacity: basemap === 'satellite' ? 0.55 : 0.7,
      color: '#fff', opacity: p.shifted ? 0.9 : 0, weight: p.shifted ? 1.2 : 0,
    }
  }

  function showLayer(name) {
    showing = name
    if (name === 'regimes' && regimeLayer) {
      map.removeLayer(cellGroup); removeHeat(); regimeLayer.addTo(map)
    } else {
      if (regimeLayer) map.removeLayer(regimeLayer)
      cellGroup.addTo(map)
      addHeat()
    }
    drawLegend()
  }

  function drawLegend() {
    if (showing === 'regimes' && regimeLayer) {
      legendElement.innerHTML = '<p class="legend-title">Fire regime of each cell</p>' +
        regimeList.map(r => `<p class="legend-row"><i style="background:${r.color}"></i>${r.name}</p>`).join('') +
        '<p class="legend-row"><i class="shifted"></i>White outline: regime changed over time</p>'
      return
    }
    if (!hotspotLegend) return
    const { breaks, metric, period } = hotspotLegend
    const format = metric === 'frp' ? formatPower : formatNumber
    const step = breaks.length > 1 ? (FIRE_RAMP.length - 1) / (breaks.length - 1) : 0
    const swatches = breaks.map((b, i) =>
      `<span><i style="background:${FIRE_RAMP[Math.round(i * step)]}"></i>${format(b)}${i === breaks.length - 1 ? '+' : ''}</span>`).join('')
    legendElement.innerHTML =
      `<p class="legend-title">${metric === 'frp' ? 'Fire energy' : 'Hotspots'} per cell, ${period}</p>` +
      `<div class="legend-ramp">${swatches}</div>`
  }

  // -------------------------------------------------------------------------
  // The selected area: a magenta box with draggable corners
  // -------------------------------------------------------------------------
  let area = null
  const WORLD_RING = [[-89, -400], [-89, 400], [89, 400], [89, -400]]
  const shade = L.polygon([WORLD_RING], {
    renderer: areaCanvas, interactive: false, stroke: false, fillColor: '#000', fillOpacity: 0.4,
  }).addTo(map)
  const box = L.rectangle([[0, 0], [1, 1]], {
    renderer: areaCanvas, interactive: false, fill: false, color: MAGENTA, weight: 2,
  }).addTo(map)
  const handleIcon = L.divIcon({ className: 'area-handle', iconSize: [20, 20] })
  const handles = [0, 1, 2, 3].map(i => {
    const handle = L.marker([0, 0], { icon: handleIcon, draggable: true, bubblingMouseEvents: false, title: 'Drag to resize the area', zIndexOffset: 500 }).addTo(map)
    handle.on('drag', () => {
      const p = handle.getLatLng()
      const opposite = handles[(i + 2) % 4].getLatLng()
      drawArea(tidyArea([p.lat, p.lng, opposite.lat, opposite.lng]), handle)
    })
    handle.on('dragend', () => onAreaChange(area))
    return handle
  })

  // sorts the corners and rounds to 0.1 degrees
  function tidyArea([latA, lonA, latB, lonB]) {
    const round = v => Math.round(v * 10) / 10
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
    return [
      round(clamp(Math.min(latA, latB), -89, 89)), round(clamp(Math.min(lonA, lonB), -180, 180)),
      round(clamp(Math.max(latA, latB), -89, 89)), round(clamp(Math.max(lonA, lonB), -180, 180)),
    ]
  }

  function drawArea(next, draggedHandle) {
    area = next
    const [s, w, n, e] = area
    box.setBounds([[s, w], [n, e]])
    shade.setLatLngs([WORLD_RING, [[s, w], [s, e], [n, e], [n, w]]])
    const corners = [[s, w], [s, e], [n, e], [n, w]]
    // while a corner is being dragged, leave that one under the pointer
    if (!draggedHandle) handles.forEach((h, i) => h.setLatLng(corners[i]))
  }

  function setArea(next, { fit = false } = {}) {
    drawArea(tidyArea([next[0], next[1], next[2], next[3]]))
    if (fit) fitArea()
  }
  function fitArea() {
    const [s, w, n, e] = area
    // leave room around the box so you can see where it sits
    map.fitBounds(L.latLngBounds([[s, w], [n, e]]).pad(0.3), { maxZoom: 7 })
  }

  // "Draw on map": drag out a new box with the mouse
  let drawing = false, drawStart = null, drawStartFromEvent = () => {}
  function startDrawing(onFinish) {
    drawing = true
    map.dragging.disable()
    element.classList.add('is-drawing')
    const cancel = () => {
      drawing = false; drawStart = null
      map.dragging.enable()
      element.classList.remove('is-drawing')
      map.off('mousedown', down); map.off('mousemove', move); map.off('mouseup', up)
      document.removeEventListener('keydown', onKey)
      onFinish()
    }
    const down = e => { drawStart = e.latlng }
    drawStartFromEvent = ev => { drawStart = map.mouseEventToLatLng(ev) }
    const move = e => {
      if (drawStart) drawArea(tidyArea([drawStart.lat, drawStart.lng, e.latlng.lat, e.latlng.lng]))
    }
    const up = e => {
      if (!drawStart) return
      const next = tidyArea([drawStart.lat, drawStart.lng, e.latlng.lat, e.latlng.lng])
      const bigEnough = next[2] - next[0] >= 0.2 && next[3] - next[1] >= 0.2
      const before = area
      swallowClick = true
      cancel()
      if (bigEnough) { drawArea(next); onAreaChange(next) } else { drawArea(before); onAreaChange(before) }
    }
    const previous = area
    const onKey = e => { if (e.key === 'Escape') { cancel(); drawArea(previous) } }
    map.on('mousedown', down); map.on('mousemove', move); map.on('mouseup', up)
    document.addEventListener('keydown', onKey)
    return cancel
  }

  const inside = ll => area && ll.lat >= area[0] && ll.lat <= area[2] && ll.lng >= area[1] && ll.lng <= area[3]
  let swallowClick = false

  // Drag the box from its middle to move it
  let moving = null
  element.addEventListener('mousedown', ev => {
    if (ev.shiftKey || drawing || ev.button !== 0 || ev.target.closest('.leaflet-marker-icon, .leaflet-control, button')) return
    const ll = map.mouseEventToLatLng(ev)
    if (!inside(ll)) return
    ev.stopPropagation()   // keep Leaflet from panning the map
    moving = { start: ll, from: area, moved: false }
    element.classList.add('is-moving')
  }, true)
  document.addEventListener('mousemove', ev => {
    if (!moving) return
    const ll = map.mouseEventToLatLng(ev)
    const dLat = ll.lat - moving.start.lat, dLon = ll.lng - moving.start.lng
    if (Math.abs(dLat) + Math.abs(dLon) > 0.05) moving.moved = true
    const [s, w, n, e] = moving.from
    const shiftLat = Math.min(89 - n, Math.max(-89 - s, dLat)), shiftLon = Math.min(180 - e, Math.max(-180 - w, dLon))
    drawArea(tidyArea([s + shiftLat, w + shiftLon, n + shiftLat, e + shiftLon]))
  })
  document.addEventListener('mouseup', () => {
    if (!moving) return
    element.classList.remove('is-moving')
    if (moving.moved) { swallowClick = true; onAreaChange(area) }
    moving = null
  })
  element.addEventListener('mousemove', ev => {
    if (moving || drawing) return
    element.classList.toggle('over-box', !!inside(map.mouseEventToLatLng(ev)))
  })

  // Click outside the box: move the box there, keeping its size
  map.on('click', e => {
    if (swallowClick) { swallowClick = false; return }
    if (drawing || !area || inside(e.latlng)) return
    const [s, w, n, e2] = area
    const halfLat = (n - s) / 2, halfLon = (e2 - w) / 2
    const lat = Math.min(89 - halfLat, Math.max(-89 + halfLat, e.latlng.lat))
    const lon = Math.min(180 - halfLon, Math.max(-180 + halfLon, e.latlng.lng))
    const next = tidyArea([lat - halfLat, lon - halfLon, lat + halfLat, lon + halfLon])
    drawArea(next)
    onAreaChange(next)
  })

  // Shift + drag: draw a new box without pressing "Draw on map" first.
  // Runs before Leaflet's own handler so the map doesn't pan.
  element.addEventListener('mousedown', ev => {
    if (!ev.shiftKey || drawing || ev.button !== 0) return
    ev.stopPropagation()
    startDrawing(() => {})
    drawStartFromEvent(ev)
  }, true)

  // -------------------------------------------------------------------------
  map.setView([56, -114], 4.5)
  drawGraticule()

  return {
    map, loadBasemap, buildCells, setCells, setRegimes, showLayer, setBasemap,
    getPlaces: () => places,
    openPopup: (html, latlng) => L.popup({ className: 'story-popup', maxWidth: 320, autoPanPadding: [20, 20] }).setLatLng(latlng).setContent(html).openOn(map),
    isShowing: () => showing,
    setArea, fitArea, startDrawing,
    isDrawing: () => drawing,
    hasRegimes: () => !!regimeLayer,
  }
}
