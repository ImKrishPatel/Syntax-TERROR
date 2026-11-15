import test from 'node:test'
import assert from 'node:assert/strict'
import {parseVoiceCommand} from '../js/voice-commands.js'
import {createVoiceNavigator} from '../js/voice-navigation.js'
import {resolveSpokenPlace} from '../js/search.js'

test('place and time commands coexist with annual playback',()=>{
 assert.deepEqual(parseVoiceCommand('Calgary from 2008 to 2022'),{type:'navigate',place:'calgary',fromYear:2008,toYear:2022})
 assert.deepEqual(parseVoiceCommand('Search Calgary'),{type:'navigate',place:'calgary',fromYear:null,toYear:null})
 assert.deepEqual(parseVoiceCommand('Time period 1995'),{type:'navigate',place:null,fromYear:1995,toYear:1995})
 assert.equal(parseVoiceCommand('2008 to 2022').type,'navigate')
 assert.equal(parseVoiceCommand('2024').type,'play')
 assert.equal(parseVoiceCommand('Calgary from two thousand eight to twenty twenty two').toYear,2022)
})
test('invalid periods and unresolved places do not change either selection',async()=>{
 const applied=[];let lookups=0
 const navigate=createVoiceNavigator({resolvePlace:async()=>{lookups++;return null},apply:x=>applied.push(x)})
 for(const phrase of ['Calgary from 1995 to 2022','Time period 2027','Time period 2000 to 2005'])assert.match(await navigate(parseVoiceCommand(phrase)),/2006 to 2026/)
 assert.equal(lookups,0)
 assert.match(await navigate(parseVoiceCommand('2022 to 2008')),/start year/)
 assert.match(await navigate(parseVoiceCommand('Search madeupplace')),/spell it/)
 assert.deepEqual(applied,[])
})
test('valid selection applies together and a superseded search cannot overwrite it',async()=>{
 const place={name:'Calgary',lat:51.05,lon:-114.06},applied=[]
 const navigate=createVoiceNavigator({resolvePlace:async()=>place,apply:x=>applied.push(x)})
 assert.match(await navigate(parseVoiceCommand('Calgary from 2008 to 2022')),/Calgary.*2008 to 2022/)
 assert.deepEqual(applied,[{place,fromYear:2008,toYear:2022}])
 assert.equal(await navigate(parseVoiceCommand('Search Calgary'),()=>false),null)
 assert.equal(applied.length,1)
 await navigate(parseVoiceCommand('Time period 2006 to 2026'))
 assert.equal(applied[1].place,null)
})
test('voice place resolver rejects fuzzy results and accepts qualified names',async()=>{
 const calgary={name:'Calgary',detail:'Alberta, Canada',lat:51.05,lon:-114.06}
 const options={getSaved:()=>[],getOffline:()=>[],online:async()=>[calgary]}
 assert.equal(await resolveSpokenPlace('Calgary Canada',options),calgary)
 assert.equal(await resolveSpokenPlace('Calgarbage',options),null)
 assert.equal((await resolveSpokenPlace('Calgary',{...options,online:async()=>[calgary,{...calgary,lat:57}]})).choices.length,2)
 assert.equal(await resolveSpokenPlace('Calgary',{...options,getOffline:()=>[calgary],online:async()=>{throw Error('offline')}}),calgary)
})

test('natural search phrasing and accents produce clean queries',()=>{
 for(const phrase of ['Search for Calgary','Please search for Calgary please','Show me Calgary','Take me to Calgary'])assert.equal(parseVoiceCommand(phrase).place,'calgary')
 assert.equal(parseVoiceCommand('Search Montréal').place,'montreal')
 assert.equal(parseVoiceCommand('Search for Calgary from 2008 to 2022').place,'calgary')
})
test('minor misspellings and alternative transcripts suggest instead of rejecting',async()=>{
 const city={name:'Calgary',detail:'Alberta, Canada',lat:51.05,lon:-114.06}
 const options={getSaved:()=>[],getOffline:()=>[city],online:async()=>[]}
 assert.equal((await resolveSpokenPlace('Calgery',options)).choices[0],city)
 assert.equal((await resolveSpokenPlace('call gary',{...options,alternatives:['calgary']})).choices[0],city)
 assert.equal(await resolveSpokenPlace('nonsense words',options),null)
 const duplicate={...city,lat:51.06}
 assert.equal((await resolveSpokenPlace('Calgary',{...options,getOffline:()=>[],online:async()=>[city,duplicate]})).name,'Calgary')
})
test('suggestions require confirmation and keep requested dates',async()=>{
 const city={name:'Calgary',detail:'Alberta, Canada',lat:51.05,lon:-114.06},applied=[]
 const navigate=createVoiceNavigator({resolvePlace:async()=>({choices:[city]}),apply:x=>applied.push(x)})
 assert.match(await navigate(parseVoiceCommand('Calgery from 2008 to 2022')),/Did you mean Calgary/)
 assert.equal(applied.length,0)
 await navigate(parseVoiceCommand('Yes'))
 assert.deepEqual(applied,[{place:city,fromYear:2008,toYear:2022}])
 await navigate(parseVoiceCommand('Search Calgery'));await navigate(parseVoiceCommand('No'))
 await navigate(parseVoiceCommand('Yes'));assert.equal(applied.length,1)
})

test('spelling accepts letters, letter names, country separators and multiword places',()=>{
 assert.equal(parseVoiceCommand('Search C A L G A R Y Pause C A N A D A').place,'calgary canada')
 assert.equal(parseVoiceCommand('Search C A L G A R Y').place,'calgary')
 assert.equal(parseVoiceCommand('Search see ay el gee ay are why').place,'calgary')
 assert.equal(parseVoiceCommand('Search N E W space Y O R K Pause U N I T E D space S T A T E S').place,'new york united states')
 assert.deepEqual(parseVoiceCommand('Choose C A N A D A'),{type:'choosePlace',choice:'canada'})
})
test('country ambiguity lists results and waits for a country or numbered selection',async()=>{
 const canada={name:'London',country:'Canada',detail:'Ontario, Canada',lat:42.98,lon:-81.25}
 const uk={name:'London',country:'United Kingdom',detail:'England, United Kingdom',lat:51.5,lon:-.12}
 const applied=[]
 const navigate=createVoiceNavigator({resolvePlace:async()=>({choices:[canada,uk]}),apply:s=>applied.push(s)})
 const message=await navigate(parseVoiceCommand('Search London from 2008 to 2022'))
 assert.match(message,/Canada/);assert.match(message,/United Kingdom/);assert.equal(applied.length,0)
 await navigate(parseVoiceCommand('Yes'));assert.equal(applied.length,0)
 await navigate(parseVoiceCommand('Choose Canada'))
 assert.deepEqual(applied[0],{place:canada,fromYear:2008,toYear:2022})
 await navigate(parseVoiceCommand('Search London'));await navigate(parseVoiceCommand('Choose two'))
 assert.equal(applied[1].place,uk)
})
test('online countries are checked even when offline contains one exact match',async()=>{
 const local={name:'London',lat:51.5,lon:-.12}
 const results=[{...local,country:'United Kingdom',detail:'United Kingdom'},{name:'London',lat:42.98,lon:-81.25,country:'Canada',detail:'Ontario, Canada'}]
 const options={getSaved:()=>[],getOffline:()=>[local],online:async()=>results}
 const ambiguous=await resolveSpokenPlace('London',options)
 assert.equal(ambiguous.choices.length,2)
 assert.equal((await resolveSpokenPlace('london unitedkingdom',options)).country,'United Kingdom')
})
