// The chat box's middleman server (runs on Cloudflare Workers, free).
//
// Why this exists: our website is public, so a Gemini key written in the
// website's code could be copied by anyone. Instead, the key lives here as a
// secret setting (GEMINI_KEY) that only Cloudflare can see.
//
//   website  --question + screen summary-->  this server  --+key-->  Gemini
//   website  <----------- answer -----------  this server  <-------  Gemini
//
// Setup steps are in the README under "Chat box".

// Gemini models to try, in order. If one is retired or hits the free limit,
// the next one is tried. Google renames models often: if all of them fail,
// look up current names at https://ai.google.dev/gemini-api/docs/models
const MODELS = [
  'gemini-3.5-flash-lite',
  'gemini-3.1-flash-lite',
  'gemini-flash-lite-latest',
  'gemini-3.7-flash',
  'gemini-flash-latest',
]

// Websites allowed to use this server. Any localhost port is allowed so
// everyone on the team can run the app on their own computer.
const ALLOWED_SITES = [
  'https://imkrishpatel.github.io',
]

const RULES = `You are the help chat inside Ember, a web app that shows satellite
hotspots (NASA MODIS, checked against VIIRS) for any place in the world.
Rules:
- Only use the numbers in the DATA block. Never make up numbers.
- If the answer is not in the data, say so and tell the user what to change on
  the page (pick another area, change the years, select a month in the calendar).
- Write for someone who is not a scientist. Short answers, plain words, at most
  about 120 words unless they ask for more.
- A score is in sigma: around 0 is normal, 2 or more is unusual.
- If the data says it is demo data, mention that the numbers are not real.
- Politely decline questions that have nothing to do with fires, this app or its data.`

function isAllowed(origin) {
  return ALLOWED_SITES.includes(origin) ||
    /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)
}

function reply(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...headers, 'Content-Type': 'application/json' },
  })
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || ''
    const headers = {
      'Access-Control-Allow-Origin': isAllowed(origin) ? origin : 'null',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Vary': 'Origin',
    }

    // the browser asks "may I?" before the real request
    if (request.method === 'OPTIONS') return new Response(null, { headers })
    if (!isAllowed(origin)) return reply({ error: 'Not allowed' }, 403, headers)
    if (!env.GEMINI_KEY) return reply({ error: 'GEMINI_KEY is not set on the server' }, 500, headers)

    // GET /models lists the Gemini models our key can use (handy when Google renames them)
    if (request.method === 'GET' && new URL(request.url).pathname === '/models') {
      const list = await fetch('https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000', {
        headers: { 'x-goog-api-key': env.GEMINI_KEY },
      }).then(r => r.json()).catch(() => ({}))
      const names = (list.models || [])
        .filter(m => (m.supportedGenerationMethods || []).includes('generateContent'))
        .map(m => m.name.replace('models/', ''))
      return reply({ models: names }, 200, headers)
    }
    if (request.method !== 'POST') return reply({ error: 'Not allowed' }, 405, headers)

    let body
    try { body = await request.json() } catch (err) { return reply({ error: 'Bad request' }, 400, headers) }

    // keep requests small: last few messages, limited length
    const messages = (Array.isArray(body.messages) ? body.messages : []).slice(-13)
    if (!messages.length) return reply({ error: 'No question' }, 400, headers)
    const contents = messages.map(m => ({
      role: m.role === 'model' ? 'model' : 'user',
      parts: [{ text: String(m.text || '').slice(0, 2000) }],
    }))
    const data = String(body.data || '').slice(0, 60000)
    const last = contents[contents.length - 1].parts[0]
    last.text = `DATA (what the user sees right now):\n${data}\n\nQUESTION: ${last.text}`

    const makeBody = shortThinking => JSON.stringify({
      system_instruction: { parts: [{ text: RULES }] },
      contents,
      // newer models "think" before answering; keep it short so answers come fast
      generationConfig: shortThinking
        ? { temperature: 0.3, thinkingConfig: { thinkingLevel: 'low' } }
        : { temperature: 0.3 },
    })

    // for testing one model: send { "model": "<name>" } (only names from MODELS)
    const models = MODELS.includes(body.model) ? [body.model] : MODELS

    let result = null, busy = false
    const tried = []   // what happened with each model, sent back when all of them fail
    for (const model of models) {
      let shortThinking = true
      for (let attempt = 0; attempt < 2; attempt++) {
        const started = Date.now()
        let gemini
        try {
          gemini = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'x-goog-api-key': env.GEMINI_KEY },
            body: makeBody(shortThinking),
            signal: AbortSignal.timeout(15000),   // give up on a model after 15 seconds
          })
        } catch (err) {
          tried.push(`${model}: no answer after ${Math.round((Date.now() - started) / 1000)}s`)
          break
        }
        const answer = await gemini.json().catch(() => ({}))
        if (gemini.ok) { result = answer; break }
        const message = (answer.error && answer.error.message) || ''
        // some models don't accept the short-thinking setting: ask once more without it
        if (gemini.status === 400 && shortThinking && /think/i.test(message)) { shortThinking = false; continue }
        tried.push(`${model}: ${gemini.status} ${message}`.trim())
        busy = gemini.status === 429
        break
      }
      if (result) break
      // a bad key fails on every model, so stop trying
      if (/: 40[13] /.test(tried[tried.length - 1] + ' ')) break
    }
    if (!result) return reply({ error: busy ? 'busy' : 'failed', tried }, busy ? 429 : 502, headers)

    const parts = (result.candidates && result.candidates[0] && result.candidates[0].content &&
      result.candidates[0].content.parts) || []
    const answer = parts.map(p => p.text || '').join('').trim()
    return reply({ answer: answer || 'Sorry, I could not come up with an answer. Try asking another way.' }, 200, headers)
  },
}
