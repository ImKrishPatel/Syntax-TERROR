// Small things the browser remembers between visits: light or dark, the
// colour-blind palette, favourites, and so on. They are kept in localStorage,
// which only lives in this browser. If storage is blocked the app still works,
// it just forgets.

const PREFIX = 'ember.'

export function loadPref(name, fallback) {
  try {
    const text = localStorage.getItem(PREFIX + name)
    return text === null ? fallback : JSON.parse(text)
  } catch (err) {
    return fallback
  }
}

export function savePref(name, value) {
  try {
    localStorage.setItem(PREFIX + name, JSON.stringify(value))
  } catch (err) { /* storage may be blocked: nothing to do */ }
}

// The theme is read by a tiny script in index.html before the page draws,
// so it is stored as plain text ('light' or 'dark') instead of JSON.
export function saveTheme(theme) {
  try { localStorage.setItem(PREFIX + 'theme', theme) } catch (err) { /* fine */ }
}
