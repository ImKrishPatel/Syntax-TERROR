// Voice searches must not expand a city to fit the coarse fire-data grid.
export function voicePlaceBounds(place) {
  const validBox=place.box?.length===4 && place.box.every(Number.isFinite) && place.box[0]<place.box[2] && place.box[1]<place.box[3]
  const box=validBox ? place.box : [place.lat-.08,place.lon-.12,place.lat+.08,place.lon+.12]
  return box.map((v,i)=>Math.round(Math.max(i%2?-180:-89,Math.min(i%2?180:89,v))*10000)/10000)
}
