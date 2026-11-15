import test from 'node:test'
import assert from 'node:assert/strict'
import { listeningMonths, describeMonth, seasonSummary, strongestMonth } from '../js/listening-model.js'
const table={years:[2023],metric:'count',value:[[0,100,NaN,...Array(9).fill(2)]],score:[[0,3,NaN,...Array(9).fill(-1)]],estimatedShare:[Array(12).fill(0)],usual:Array.from({length:12},()=>({years:5,sd:2,mean:5}))}
test('coverage overrides zeros; missing months remain missing',()=>{
 assert.equal(listeningMonths(table,2023,false)[0].status,'missing')
 const months=listeningMonths(table,2023)
 assert.match(describeMonth(months[0]),/0 hotspots/)
 assert.match(describeMonth(months[2]),/No data available/)
 assert.match(seasonSummary(months),/incomplete year/)
})
test('no variation or single year has unavailable comparison',()=>{
 const copy={...table,usual:table.usual.map(u=>({...u,sd:0}))}
 assert.equal(listeningMonths(copy,2023)[0].anomaly,null)
 assert.equal(strongestMonth(listeningMonths(copy,2023)),null)
})
test('anomaly shortcut uses scores, labels estimated values and correct FRP units',()=>{
 assert.equal(strongestMonth(listeningMonths(table,2023)).month,1)
 const copy={...table,metric:'frp',estimatedShare:[Array(12).fill(1)]}
 assert.match(describeMonth(listeningMonths(copy,2023)[1]),/megawatts/)
 assert.match(describeMonth(listeningMonths(copy,2023)[1]),/Modeled estimate/)
})
