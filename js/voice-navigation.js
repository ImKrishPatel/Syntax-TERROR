const normalize=text=>String(text).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim()
const SPELL_HELP='I could not identify that place. Click Speak again and spell it. For example, say Search C A L G A R Y. To add the country, say the word Pause, then C A N A D A.'
const placeLabel=p=>`${p.name}${p.detail ? ', '+p.detail : p.country ? ', '+p.country : ` near latitude ${p.lat.toFixed(2)}, longitude ${p.lon.toFixed(2)}`}`

// Validate the entire request before changing either the place or the years.
export function createVoiceNavigator({resolvePlace, apply, firstYear=2006, lastYear=2026}) {
  let pending=null
  const listChoices=choices=>{
    const countries=[...new Set(choices.map(p=>p.country).filter(Boolean))]
    if(countries.length>1 && choices.every(p=>p.country))
      return `I found matches in these countries. ${countries.join('. ')}. Click Speak again and say Choose followed by the country. You can also search again with a spelled place and country.`
    return `I found these places. ${choices.map((p,i)=>`Option ${i+1}, ${placeLabel(p)}.`).join(' ')} Click Speak again and say Choose one, Choose two, or another option number.`
  }
  const select=(place,fromYear,toYear)=>{
    apply({place,fromYear,toYear})
    return `${place ? `Selected ${placeLabel(place)}. ` : ''}${fromYear!==null ? `Time period set to ${fromYear}${fromYear===toYear?'':` to ${toYear}`}.` : ''}`.trim()
  }
  return async (command, isCurrent=()=>true) => {
    if(command.type==='cancelPlace') {pending=null;return null}
    if(command.type==='confirmPlace' || command.type==='choosePlace') {
      if(!pending || Date.now()-pending.created>120000) {pending=null;return 'No place is waiting for selection. Say Search followed by a place.'}
      if(!isCurrent())return null
      if(command.type==='confirmPlace' && !command.accept) {pending=null;return 'Search cancelled. Say Search followed by another place.'}
      let place
      if(command.type==='confirmPlace') {
        if(pending.choices.length!==1)return listChoices(pending.choices)
        place=pending.choices[0]
      } else {
        const choice=normalize(command.choice).replace(/^option /,'')
        const number=({one:1,two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,nine:9,ten:10})[choice] || (/^\d+$/.test(choice)?Number(choice):0)
        if(number)place=pending.choices[number-1]
        else {
          const found=pending.choices.filter(p=>normalize(p.country || '').replace(/ /g,'')===choice.replace(/ /g,'') || normalize(p.detail || '')===choice)
          if(found.length>1) {pending.choices=found;return listChoices(found)}
          place=found[0]
        }
        if(!place)return `That choice was not in the results. ${listChoices(pending.choices)}`
      }
      const {fromYear,toYear}=pending;pending=null
      return select(place,fromYear,toYear)
    }
    pending=null
    const {fromYear,toYear}=command
    if(fromYear!==null && (!Number.isInteger(fromYear) || !Number.isInteger(toYear) || fromYear<firstYear || toYear>lastYear || toYear<firstYear || fromYear>lastYear))
      return `Our data supports ${firstYear} to ${lastYear}. Choose a time period within those years.`
    if(fromYear!==null && fromYear>toYear)return 'The start year must come before or equal the end year. Try again.'
    let place=null
    if(command.place) {
      const controller=new AbortController()
      const timeout=setTimeout(()=>controller.abort(),8000)
      try {place=await resolvePlace(command.place,controller.signal,command.placeAlternatives || [])} catch {} finally {clearTimeout(timeout)}
      if(!isCurrent())return null
      if(!place)return SPELL_HELP
      if(place.choices) {
        pending={created:Date.now(),choices:place.choices,fromYear,toYear}
        if(place.choices.length>1)return listChoices(place.choices)
        return `Did you mean ${placeLabel(place.choices[0])}? Click Speak again and say Yes or No. You can also spell the place and country.`
      }
    }
    if(!isCurrent())return null
    return select(place,fromYear,toYear)
  }
}
