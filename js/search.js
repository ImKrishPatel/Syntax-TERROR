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
async function searchOnline(query, signal, limit=CONFIG.search.results) {
  const url = `${CONFIG.search.url}?q=${encodeURIComponent(query)}&limit=${limit}&lang=en`
  const response = await fetch(url, { signal })
  if (!response.ok) return []
  const result = await response.json()
  const places = []
  for (const feature of result.features || []) {
    const p = feature.properties || {}
    if (!p.name) continue
    const [lon, lat] = feature.geometry.coordinates
    const place = { kind:p.osm_key, country:p.country || '', state:p.state || '', name: p.name, detail: [p.state, p.country].filter(part => part && part !== p.name).join(', '), lat, lon }
    // Photon gives the outline of bigger places as [west, north, east, south]
    if (p.extent && p.extent[0] < p.extent[2]) {
      place.box = [p.extent[3], p.extent[0], p.extent[1], p.extent[2]]
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


const voiceNormalize=text=>String(text).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim().replace(/\bst\b/g,'saint').replace(/\bmt\b/g,'mount')
function nameSimilarity(a,b) {
  a=a.replace(/ /g,'');b=b.replace(/ /g,'')
  if(a===b)return 1
  if(Math.min(a.length,b.length)<5)return 0
  let row=Array.from({length:b.length+1},(_,i)=>i)
  for(let i=1;i<=a.length;i++) {
    const next=[i]
    for(let j=1;j<=b.length;j++)next[j]=Math.min(next[j-1]+1,row[j]+1,row[j-1]+(a[i-1]===b[j-1]?0:1))
    row=next
  }
  return 1-row[b.length]/Math.max(a.length,b.length)
}

// Exact names select directly; close/ambiguous matches require spoken confirmation.
export async function resolveSpokenPlace(query, {getSaved, getOffline, signal, online=(q,s)=>searchOnline(q,s,30), alternatives=[]}) {
  const queries=[...new Set([query,...alternatives].map(voiceNormalize))].filter(q=>q.length>=2).slice(0,3)
  if(!queries.length)return null
  const score=(place,wanted)=>{
    const name=voiceNormalize(place.name), detail=voiceNormalize(place.detail || '')
    if(name===wanted)return 1
    const compactName=name.replace(/ /g,''), compactWanted=wanted.replace(/ /g,'')
    const suffix=compactWanted.startsWith(compactName)?compactWanted.slice(compactName.length):''
    if(suffix && [place.country,place.state,place.detail].filter(Boolean).some(value=>voiceNormalize(value).replace(/ /g,'')===suffix))return 1
    if(wanted.startsWith(name+' ') && wanted.slice(name.length+1).split(' ').every(word=>detail.split(' ').includes(word)))return 1
    return nameSimilarity(wanted,name)
  }
  const valid=p=>p.name && (!p.kind || ['place','boundary','natural'].includes(p.kind)) && Number.isFinite(p.lat) && Number.isFinite(p.lon) && Math.abs(p.lat)<=90 && Math.abs(p.lon)<=180
  const local=[...getSaved(),...getOffline()].filter(valid)
  function ranked(places) {
    const list=places.map(place=>({place,score:Math.max(...queries.map((q,i)=>score(place,q)-(i?0.02:0)))})).filter(p=>p.score>=0.72).sort((a,b)=>b.score-a.score)
    const unique=[]
    for(const candidate of list) {
      if(!unique.some(other=>voiceNormalize(other.place.name)===voiceNormalize(candidate.place.name) && Math.abs(other.place.lat-candidate.place.lat)<0.25 && Math.abs(other.place.lon-candidate.place.lon)<0.25))unique.push(candidate)
    }
    return unique
  }
  // Check online even for a local exact match so namesakes in other countries are visible.
  let results=[]
  results=(await Promise.all(queries.map(async q=>{
    try {return await online(q,signal)} catch {return []}
  }))).flat().filter(valid)
  const candidates=ranked([...results,...local])
  if(!candidates.length)return null
  if(candidates[0].score===1 && candidates.filter(p=>p.score===1).length===1)return candidates[0].place
  const exact=candidates.filter(p=>p.score===1)
  return {choices:(exact.length ? exact : candidates.slice(0,3)).map(p=>p.place)}
}
