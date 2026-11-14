// leaflet-draw is an old-style plugin that adds itself to a global `L`.
// We give it a plain copy of Leaflet that it is allowed to modify.
// Import L from this file (not from 'leaflet') when you need L.Control.Draw.
import * as Leaflet from 'leaflet'
import 'leaflet/dist/leaflet.css'

const L = { ...Leaflet }
window.L = L

export default L
