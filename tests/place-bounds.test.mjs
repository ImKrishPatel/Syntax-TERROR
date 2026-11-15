import test from 'node:test'
import assert from 'node:assert/strict'
import {voicePlaceBounds} from '../js/place-bounds.js'
test('Calgary voice bounds stay local and exclude neighbouring cities',()=>{
 const [s,w,n,e]=voicePlaceBounds({lat:51.05,lon:-114.07})
 assert.ok(n-s<.2 && e-w<.3)
 for(const [lat,lon] of [[52.27,-113.81],[53.55,-113.49],[50.04,-110.68],[49.7,-112.84]])assert.equal(lat>=s && lat<=n && lon>=w && lon<=e,false)
})
test('real municipal and province bounds are preserved instead of expanded to data-grid size',()=>{
 const city={lat:51.05,lon:-114.07,box:[50.84,-114.32,51.21,-113.85]}
 assert.deepEqual(voicePlaceBounds(city),city.box)
 const province={lat:55,lon:-115,box:[49,-120,60,-110]}
 assert.deepEqual(voicePlaceBounds(province),province.box)
})
