// Preferences apply only to this open page. A fresh load starts from defaults.
// Do not read or erase old localStorage entries: other versions may still use them.
const preferences = new Map()

export function loadPref(name, fallback) {
  return preferences.has(name) ? preferences.get(name) : fallback
}

export function savePref(name, value) {
  preferences.set(name, value)
}

export function saveTheme(theme) {
  savePref('theme', theme)
}
