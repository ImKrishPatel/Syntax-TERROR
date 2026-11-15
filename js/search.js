// The search box (there are two: a big one on Home and a small one in the top bar).
//
// As you type, the list under the box fills from three places:
//   1. coordinates, if what you typed looks like numbers ("56.7, -111.4")
//   2. your favourites and the suggested areas, by name
//   3. place names from Photon, a free search of OpenStreetMap
// If Photon cannot be reached (no internet), it uses the city list that ships with the app.
//
// Every result is a "place": { name, detail, lat, lon, box }.
// box is [south, west, north, east] and may be missing (then the app makes one around lat, lon).

import { CONFIG } from './config.js'

const round1 = v => Math.round(v * 10) / 10

function degrees(v, positive, negative) {
  return `${Math.abs(round1(v))}°${v < 0 ? negative : positive}`
}

// "56.7, -111.4" is a point. "55, -119, 59.5, -110" is south, west, north, east.
export function readCoordinates(text) {
  if (!/^[\s\d.,;+-]+$/.test(text)) return null
  const numbers = text.split(/[\s,;]+/).filter(Boolean).map(Number)
  if (numbers.some(n => !Number.isFinite(n))) return null
  if (numbers.length === 2) {
    const [lat, lon] = numbers
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null
    return { name: `${degrees(lat, 'N', 'S')}, ${degrees(lon, 'E', 'W')}`, detail: 'Go to this point', lat, lon }
  }
  if (numbers.length === 4) {
    const [s, w, n, e] = numbers
    if (s >= n || w >= e || s < -90 || n > 90 || w < -180 || e > 180) return null
    return { name: 'Custom area', detail: `${degrees(s, 'N', 'S')} to ${degrees(n, 'N', 'S')}, ${degrees(w, 'E', 'W')} to ${degrees(e, 'E', 'W')}`, lat: (s + n) / 2, lon: (w + e) / 2, box: [s, w, n, e] }
  }
  return null
}

// ask Photon for places. Returns [] if anything goes wrong.
async function searchOnline(query, signal) {
  const url = `${CONFIG.search.url}?q=${encodeURIComponent(query)}&limit=${CONFIG.search.results}&lang=en`
  const response = await fetch(url, { signal })
  if (!response.ok) return []
  const result = await response.json()
  const places = []
  for (const feature of result.features || []) {
    const p = feature.properties || {}
    if (!p.name) continue
    const [lon, lat] = feature.geometry.coordinates
    const place = { name: p.name, detail: [p.state, p.country].filter(part => part && part !== p.name).join(', '), lat, lon }
    // Photon gives the outline of bigger places as [west, north, east, south]
    if (p.extent && p.extent[0] < p.extent[2]) {
      place.box = [p.extent[3], p.extent[0], p.extent[1], p.extent[2]].map(round1)
    }
    places.push(place)
  }
  return places
}

// input: the <input>. list: the <ul> under it.
// getSaved(): favourites and suggestions as places. getOffline(): the built-in city list.
// onPick(place) is called when a result is chosen.
export function wireSearch(input, list, { getSaved, getOffline, onPick }) {
  let hits = []
  let marked = 0            // which result the arrow keys are on
  let request = null        // the Photon request in flight, so a newer one can cancel it
  let timer = null

  function close() {
    list.hidden = true
    input.setAttribute('aria-expanded', 'false')
  }

  function show(note) {
    list.innerHTML = hits.map((place, i) =>
      `<li role="option" data-i="${i}" aria-selected="${i === marked}"><b>${place.name}</b><span>${place.detail || ''}</span></li>`).join('') +
      (note ? `<li class="none">${note}</li>` : '')
    list.hidden = !hits.length && !note
    input.setAttribute('aria-expanded', String(!list.hidden))
  }

  function pick(place) {
    if (!place) return
    input.value = ''
    close()
    input.blur()
    onPick(place)
  }

  function matchesByName(places, q) {
    return places.filter(p => p.name && p.name.toLowerCase().includes(q))
      .sort((a, b) => (a.name.toLowerCase().startsWith(q) ? 0 : 1) - (b.name.toLowerCase().startsWith(q) ? 0 : 1))
  }

  async function search() {
    const text = input.value.trim()
    if (request) request.abort()
    if (text.length < 2) { hits = []; close(); return }

    const typed = readCoordinates(text)
    if (typed) { hits = [typed]; marked = 0; show(); return }

    const q = text.toLowerCase()
    const saved = matchesByName(getSaved(), q).slice(0, 3)
    hits = saved; marked = 0
    show('Searching…')

    let found = []
    request = new AbortController()
    try {
      found = await searchOnline(text, request.signal)
    } catch (err) {
      if (err.name === 'AbortError') return     // a newer search took over
    }
    if (!found.length) {
      // no internet, or nothing found: try the city list that ships with the app
      const seen = new Set()
      found = matchesByName(getOffline(), q)
        .filter(p => !seen.has(p.name) && seen.add(p.name))
        .slice(0, CONFIG.search.results)
        .map(p => ({ name: p.name, detail: `${degrees(p.lat, 'N', 'S')}, ${degrees(p.lon, 'E', 'W')}`, lat: p.lat, lon: p.lon }))
    }
    if (input.value.trim() !== text) return
    hits = [...saved, ...found]
    show(hits.length ? '' : 'Nothing found. Try a nearby city, or type coordinates like 56.7, -111.4')
  }

  input.addEventListener('input', () => {
    clearTimeout(timer)
    timer = setTimeout(search, 250)     // wait until the typing pauses
  })
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!hits.length) return
      event.preventDefault()
      marked = (marked + (event.key === 'ArrowDown' ? 1 : hits.length - 1)) % hits.length
      show()
    }
    if (event.key === 'Enter') { event.preventDefault(); pick(hits[marked]) }
    if (event.key === 'Escape') close()
  })
  list.addEventListener('mousedown', event => {
    const item = event.target.closest('li[data-i]')
    if (item) { event.preventDefault(); pick(hits[Number(item.dataset.i)]) }
  })
  input.addEventListener('focus', () => { if (hits.length) show() })
  input.addEventListener('blur', () => setTimeout(close, 150))
}
