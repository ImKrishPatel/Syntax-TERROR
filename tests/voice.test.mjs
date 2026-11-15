import test from 'node:test'
import assert from 'node:assert/strict'
import { parseVoiceCommand, attachMicrophone, KEYWORD_GUIDE } from '../js/voice-commands.js'
import { createListeningPanel } from '../js/sound.js'
test('all requested keywords, spoken years and invalid phrases',()=>{
 for(const input of ['2024 Activity','2024','twenty twenty four activity','two thousand and twenty-four activity']) assert.deepEqual(parseVoiceCommand(input),{type:'play',year:2024,month:null,mode:'activity',metric:'count'})
 assert.deepEqual(parseVoiceCommand('April 2024 Unusual Activity.'),{type:'play',year:2024,month:3,mode:'anomaly',metric:'count'})
 assert.deepEqual(parseVoiceCommand('April 2024 Activity'),{type:'play',year:2024,month:3,mode:'activity',metric:'count'})
 assert.equal(parseVoiceCommand('2024 Unusual Acitivty').mode,'anomaly')
 assert.equal(parseVoiceCommand('2024 Season Overview').type,'summary')
 assert.equal(parseVoiceCommand('2024 Strongest Ano').type,'anomaly')
 assert.equal(parseVoiceCommand('Sound Guide').type,'guide')
 assert.equal(parseVoiceCommand('Keyword guide').type,'keywords')
 assert.equal(parseVoiceCommand('Leave').type,'leave')
 assert.equal(parseVoiceCommand('2024 Strongest Anomaly'),null)
 for(const phrase of ['2024 Fire Energy','April 2024 Fire Energy','2024 Fire Energy Unusual','April 2024 Fire Energy Unusual']) {
   const c=parseVoiceCommand(phrase);assert.equal(c.metric,'frp');assert.equal(c.mode,phrase.endsWith('Unusual')?'anomaly':'activity');assert.equal(c.month,phrase.startsWith('April')?3:null)
 }
 for(const [word,speed] of [['Slow',.75],['Normal',1],['Fast',1.5]]){assert.equal(parseVoiceCommand(`Playback ${word}`),null);assert.equal(parseVoiceCommand(word).speed,speed)}
 for(const bad of ['play everything','replay','go back','2024 activity please','April Activity','2024 sound guide'])assert.equal(parseVoiceCommand(bad),null)
})
function element(){return {value:'',checked:false,disabled:false,textContent:'',hidden:true,children:[],setAttribute(){},classList:{add(){},remove(){}},appendChild(b){this.children.push(b)},replaceChildren(...children){this.children=children}}}
test('commands force speech and dispatch year, month, anomaly, summary and ordered guide correctly',async()=>{
 const elements=new Map();const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)}
 globalThis.document={getElementById:get,createElement:element}
 globalThis.window={speechSynthesis:{}}
 globalThis.Option=class{constructor(label,value){this.value=value;this.text=label}}
 get('audioSpeed').value='1';get('audioVolume').value='0.6';get('audioMode').value='activity'
 const plays=[];let voice;let panel;let focused=null;let mode='amount';let opened=0;let voiceStarts=0
 const table={years:[2023,2024],metric:'count',value:[Array(12).fill(10),Array(12).fill(20)],score:[Array(12).fill(0),[0,0,0,1,0,3,0,0,2.5,0,0,0]],estimatedShare:[Array(12).fill(0),Array(12).fill(0)],usual:Array.from({length:12},()=>({years:2,sd:3,mean:15}))}
 const update=()=>panel.update(table,focused,`same-area-${table.metric}`,true,mode)
 panel=createListeningPanel({onOpen:()=>{opened++},onVoiceStart:()=>{voiceStarts++},onPick:(year,month)=>{focused={year,month};update()},onHighlight(){},onMode:newMode=>{panel.stop();mode=newMode;update()},onMetric:metric=>{panel.stop();table.metric=metric;update()},audioPlayer:{stop(){},ready:async()=>{},play:async options=>{plays.push(options);return true}},microphoneFactory:options=>{voice=options;return {cancel(){}}}})
 update()
 await voice.onCommand(parseVoiceCommand('2024 Activity'))
 assert.equal(plays.length,12);assert.ok(plays.every(p=>p.speech));assert.match(plays[0].text,/January 2024/);assert.match(plays[11].text,/December 2024/)
 assert.equal(get('audioSpeech').checked,true)
 plays.length=0
 await voice.onCommand(parseVoiceCommand('April 2024 Unusual Activity'))
 assert.equal(plays.length,1);assert.match(plays[0].text,/April 2024/);assert.equal(mode,'score')
 plays.length=0
 await voice.onCommand(parseVoiceCommand('2024 Strongest Ano'))
 assert.equal(plays.length,1);assert.match(plays[0].text,/June 2024/);assert.deepEqual(plays[0].frequencies,[330,440,550]);assert.deepEqual(focused,{year:2024,month:5})
 plays.length=0
 await voice.onCommand(parseVoiceCommand('Fast'))
 assert.equal(get('audioSpeed').value,'1.5')
 await voice.onCommand(parseVoiceCommand('2024 Season Overview'))
 assert.equal(plays.at(-1).sentencePauseMs,500);assert.equal(plays.at(-1).speechRate,1.5);assert.deepEqual(plays.at(-1).frequencies,[])
 plays.length=0
 await voice.onCommand(parseVoiceCommand('Sound Guide'))
 assert.deepEqual(plays.map(p=>p.text),['Low activity','High activity','Zero activity','Above usual','Below usual','Typical','Missing data','Comparison unavailable'])
 plays.length=0
 await voice.onCommand(parseVoiceCommand('April 2024 Fire Energy Unusual'))
 assert.equal(table.metric,'frp');assert.equal(mode,'score');assert.equal(plays.length,1);assert.match(plays[0].text,/megawatts/)
 plays.length=0
 await voice.onCommand(parseVoiceCommand('2024 Fire Energy'))
 assert.equal(plays.length,12);assert.ok(plays.every(p=>/megawatts/.test(p.text)))
 for(const phrase of ['2024 Activity','2024 Unusual Activity','April 2024 Activity','April 2024 Unusual Activity','2024 Fire Energy','2024 Fire Energy Unusual','April 2024 Fire Energy','April 2024 Fire Energy Unusual']) {
   plays.length=0
   await voice.onCommand(parseVoiceCommand(phrase))
   const energy=phrase.includes('Fire Energy'), unusual=phrase.includes('Unusual'), month=phrase.startsWith('April')
   assert.equal(plays.length,month?1:12,phrase)
   assert.ok(plays.every(p=>p.speech && p.volume>0),phrase)
   assert.equal(table.metric,energy?'frp':'count',phrase)
   assert.equal(mode,unusual?'score':'amount',phrase)
   assert.match(plays[0].text,month?/April 2024/:/January 2024/,phrase)
   assert.equal(plays[0].text.includes('megawatts'),energy,phrase)
 }
 plays.length=0
 await voice.onCommand(parseVoiceCommand('Keyword Guide'))
 assert.equal(plays.length,KEYWORD_GUIDE.length);assert.ok(plays.every(p=>p.speech && p.frequencies.length===0));assert.equal(get('keywordGuide').open,true);assert.equal(opened,1)
 plays.length=0
 const oldFocus={...focused};voice.onStart();assert.equal(voiceStarts,1);await voice.onCommand(parseVoiceCommand('Leave'))
 assert.equal(plays.length,0);assert.deepEqual(focused,oldFocus);assert.match(get('voiceStatus').textContent,/cancelled/)
 plays.length=0
 await voice.onCommand(parseVoiceCommand('1999 Activity'))
 assert.equal(plays.length,1);assert.match(plays[0].text,/outside the selected time period/)
})
test('recognition waits for end, rejects unknown input once and handles denied access',()=>{
 globalThis.window={speechSynthesis:{},addEventListener(){}}
 let rec;class Recognition{constructor(){rec=this}start(){}stop(){}abort(){}}
 const messages=[],commands=[];const button=element(),status=element()
 const mic=attachMicrophone({button,status,Recognition,onStart(){},onCommand:c=>commands.push(c),onMessage:m=>messages.push(m)})
 button.onclick();rec.onresult({results:[[{transcript:'2024 activity'}]]});assert.equal(commands.length,0);rec.onend();assert.equal(commands.length,1)
 button.onclick();rec.onresult({results:[[{transcript:'banana'}]]});rec.onend();rec.onend();assert.deepEqual(messages,['Try again with right keyword'])
 button.onclick();rec.onerror({error:'not-allowed'});rec.onend();assert.match(messages.at(-1),/Microphone access was not allowed/)
 mic.cancel()
})

test('recognized commands report synchronous and asynchronous playback failures',async()=>{
 globalThis.window={speechSynthesis:{},addEventListener(){}}
 let rec;class Recognition{constructor(){rec=this}start(){}stop(){}abort(){}}
 const originalError=console.error;console.error=()=>{}
 try {
   for(const handler of [()=>{throw new Error('missing callback')},async()=>{throw new Error('playback failed')}]) {
     const messages=[],button=element(),status=element()
     const mic=attachMicrophone({button,status,Recognition,onStart(){},onCommand:handler,onMessage:m=>messages.push(m)})
     button.onclick();rec.onresult({results:[[{transcript:'2024 Fire Energy'}]]});rec.onend()
     await new Promise(resolve=>setImmediate(resolve))
     assert.match(status.textContent,/recognized, but playback could not start/)
     assert.equal(messages.length,1)
     assert.equal(messages[0],status.textContent)
     mic.cancel()
   }
 } finally {console.error=originalError}
})

test('Speak shortcut starts recognition once and respects disabled and held keys',()=>{
 const listeners={};globalThis.window={speechSynthesis:{},addEventListener:(name,handler)=>{listeners[name]=handler}}
 let starts=0;class Recognition{start(){starts++}stop(){}abort(){}}
 const button=element();button.click=()=>button.onclick()
 const mic=attachMicrophone({button,status:element(),Recognition,onStart(){},onCommand(){},onMessage(){}})
 const key=overrides=>listeners.keydown({code:'KeyM',altKey:true,shiftKey:true,preventDefault(){},...overrides})
 key({altKey:false});key({ctrlKey:true});key({repeat:true});key({isComposing:true})
 button.disabled=true;key();assert.equal(starts,0)
 button.disabled=false;key();assert.equal(starts,1)
 key();assert.equal(starts,1)
 mic.cancel();key();assert.equal(starts,2)
 mic.cancel()
})
