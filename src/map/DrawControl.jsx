// Adds the "draw rectangle" and "draw polygon" buttons to the map.
// When the user finishes a shape it becomes the selected area.
import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import L from './leaflet.js'
import 'leaflet-draw'
import 'leaflet-draw/dist/leaflet.draw.css'
import { useSelection } from '../state/selection.js'

const SHAPE_STYLE = { color: '#ff7a1a', weight: 2 }

export default function DrawControl() {
  const map = useMap()
  const { setArea } = useSelection()

  useEffect(() => {
    const control = new L.Control.Draw({
      position: 'topleft',
      draw: {
        // showArea: false avoids a known crash in leaflet-draw 1.0.4
        rectangle: { shapeOptions: SHAPE_STYLE, showArea: false },
        polygon: { shapeOptions: SHAPE_STYLE, showArea: false, allowIntersection: false },
        polyline: false,
        circle: false,
        circlemarker: false,
        marker: false,
      },
      edit: false,
    })
    map.addControl(control)

    function onCreated(e) {
      if (e.layerType === 'rectangle') {
        const b = e.layer.getBounds()
        setArea({ type: 'bbox', bounds: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()] })
      } else {
        const points = e.layer.getLatLngs()[0].map((p) => [p.lat, p.lng])
        setArea({ type: 'polygon', points })
      }
    }
    map.on(L.Draw.Event.CREATED, onCreated)

    return () => {
      map.off(L.Draw.Event.CREATED, onCreated)
      map.removeControl(control)
    }
  }, [map, setArea])

  return null
}
