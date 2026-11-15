import test from 'node:test'
import assert from 'node:assert/strict'

test('settings apply within a page but a new page ignores previously saved choices',async()=>{
 globalThis.localStorage={getItem(){throw new Error('Must not restore browser storage')},setItem(){throw new Error('Must not persist preferences')}}
 const first=await import('../js/prefs.js?visit=first')
 first.savePref('safeColors',true);first.savePref('speech',true);first.saveTheme('dark')
 assert.equal(first.loadPref('safeColors',false),true)
 const reopened=await import('../js/prefs.js?visit=reopened')
 assert.equal(reopened.loadPref('safeColors',false),false)
 assert.equal(reopened.loadPref('speech',false),false)
 assert.equal(reopened.loadPref('theme','light'),'light')
 assert.deepEqual(reopened.loadPref('favourites',[]),[])
})
