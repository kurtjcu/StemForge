# Result Card ↔ Transport Bar Specification

> This document exists because the result-card / transport-bar relationship has
> been broken and re-fixed four times. Read this before touching any playback
> code. Follow the contracts exactly.

---

## Architecture overview

There are **two playback modes**. Every component uses exactly one of them.

### Mode 1: Card-driven (most components)

The **card's WaveSurfer instance plays the audio**. The transport bar is a
passive mirror — it shows the waveform, syncs its cursor, and proxies
play/pause/stop controls back to the card.

**Activated by:** passing `{ cardWs: ws }` as the 5th argument to `transportLoad()`.

```
Card play btn → ws.play()
                 ↓ ws events (timeupdate, play, pause, finish)
                 ↓ subscribed by transportLoad via _cardUnsub
                 → transport cursor + time display + play/pause icon sync

Transport play btn → _activeCardWs.playPause() → card ws plays/pauses
Transport stop btn → _activeCardWs.stop() + _clearCardLink()
```

**Used by:** Separate, Enhance, MIDI, Synth (generate.js), Mix, Export, Loader

### Mode 2: Transport-driven (compose only)

The **transport bar's own WaveSurfer plays the audio**. The card has no
independent audio playback — it proxies everything through the transport.

**Activated by:** calling `transportLoad(url, label, true, source)` with
`autoplay=true` and **no** `cardWs` option.

```
Card play btn → _claimTransport(entry) + transportLoad(url, label, true, source)
                 ↓ transport ws loads URL and auto-plays on 'ready'
                 ↓ transportOnTimeUpdate / transportOnStateChange subscriptions
                 → card timeLabel + playBtn icon sync + card ws cursor sync

Transport play btn → ws.playPause() (transport's own ws)
Transport stop btn → ws.stop()
```

**Used by:** Compose (result cards + voice cards)

Compose has its own `_claimTransport(entry, timeLabel, playBtn)` function that:
1. Unsubscribes the previous card's transport event listeners
2. Resets the previous card's play button to "▶ Play"
3. Sets `_transportOwner = entry`
4. Subscribes the new card's timeLabel and playBtn to `transportOnTimeUpdate` and `transportOnStateChange`

---

## The correct `transportLoad` call signature

```js
transportLoad(url, label, autoplay, source, opts)
```

| Param | Type | Purpose |
|-------|------|---------|
| `url` | string | Audio stream URL |
| `label` | string | Display name (shown in "Now Playing (source): label") |
| `autoplay` | boolean | `false` for card-driven (card plays audio), `true` for transport-driven (transport plays audio) |
| `source` | string | Tab/section name, e.g. `'Separate'`, `'Enhance › Tune'`, `'Compose'` |
| `opts` | object | `{ cardWs: WaveSurfer }` for card-driven mode. Omit for transport-driven. |

---

## Per-component contract

Every result card must implement **all four** of these behaviors correctly.
Missing any one of them causes the bugs we keep seeing.

### 1. Play button → load transport

When the user clicks Play on a card, the card must:
- Stop all other players in the same component (`stopOtherPlayers(ws)`)
- Start playback on the card's ws (`ws.play()`)
- Update button text to "⏸ Pause"
- Call `transportLoad(url, label, false, source, { cardWs: ws })` (card-driven)
  OR `_claimTransport(entry) + transportLoad(url, label, true, source)` (transport-driven)

### 2. Stop button → clear transport

When the user clicks Stop on a card:
- Stop the card's ws (`ws.stop()`)
- Call `transportStop()`
- Reset button text to "▶ Play"

### 3. Finish event → clear transport

When playback reaches the end (`ws.on('finish', ...)`):
- Reset button text to "▶ Play"
- Call `transportStop()`

### 4. Exclusive playback

Only one card should play at a time within a component. Before playing, stop
all other players. The transport bar handles cross-component exclusivity via
`_clearCardLink()` (which stops mirroring the previous card).

---

## Current status by component (as of 2026-03-21)

| Component | File | Mode | Play→Transport | Stop→Transport | Finish→Transport | Notes |
|-----------|------|------|:-:|:-:|:-:|-------|
| Loader | `loader.js:158` | card-driven | ✅ (auto-load, no play) | n/a | n/a | Loads on upload, no play button |
| Separate (single) | `separate.js:643-678` | card-driven | ✅ | ✅ | ✅ | Source: `'Separate › Batch'` (misleading — should be `'Separate'`) |
| Separate (ACE) | `separate.js:553-583` | card-driven | ✅ | ✅ | ✅ | Source: `'Separate'` |
| Separate (batch) | `separate.js:808-833` | **NONE** | ❌ MISSING | ❌ MISSING | ❌ MISSING | **BUG**: batch result cards don't wire transport at all |
| Enhance | `enhance.js:69-92` | card-driven | ✅ | ❌ MISSING | ❌ MISSING | **BUG**: `_createPlayer` stop btn doesn't call `transportStop()`; finish handler doesn't either. Also: `transportStop` not even imported. |
| Enhance (auto-load) | `enhance.js:778,834,1398` | transport-driven | ✅ | n/a | n/a | On job done, auto-loads result into transport (no cardWs, no autoplay) |
| MIDI | `midi.js:710,741` | card-driven | ✅ | ✅ | ✅ | Correct |
| Synth (generate) | `generate.js:123,129,139` | card-driven | ✅ | ✅ | ✅ | Correct |
| Compose | `compose.js:4068-4085` | transport-driven | ✅ | ✅ | ✅ (via transport) | Uses `_claimTransport` pattern |
| Compose (voice) | `compose.js:4482-4498` | transport-driven | ✅ | ✅ | ✅ (via transport) | Uses `_claimTransport` pattern |
| Mix (createMixPlayer) | `mix.js:68-92` | card-driven | ✅ | ✅ | ✅ | Correct |
| Mix (MIDI track) | `mix.js:422` | card-driven | ✅ | ✅ | ✅ | Correct |
| Mix (MIDI inline) | `mix.js:459` | card-driven | ✅ | ✅ | ❌ No transportStop | Minor: inline MIDI preview doesn't clear transport on finish |
| Export | `export.js:271-293` | card-driven | ✅ | ✅ | ✅ | Correct |

### Known bugs summary

1. **`enhance.js:_createPlayer`** — stop button (line 81-83) calls `ws.stop()` but not `transportStop()`. Finish handler (line 92) also omits `transportStop()`. `transportStop` is not imported. **Effect**: transport bar keeps showing "Now Playing" after enhance audio stops/finishes.

2. **`separate.js:showBatchResults`** — batch result cards (lines 808-833) have no `transportLoad` on play, no `transportStop` on stop/finish. **Effect**: batch separation results don't appear in transport bar at all.

3. **`separate.js:showStemResults`** (line 652) — source string is `'Separate › Batch'` but this is the single-file code path. Should be `'Separate'`. Cosmetic only.

---

## How to add a new result card (checklist)

When adding a new playable result card in any component:

```
□ Import { transportLoad, transportStop } from './audio-player.js'
□ Create WaveSurfer with createWaveform(container, { height: 50 })
□ Load URL: ws.load(url)
□ Add to exclusive-playback array (e.g. _players.push({ ws, playBtn }))

□ Play button click handler:
    □ If playing → ws.pause(), set "▶ Play"
    □ If not playing → stopOtherPlayers(ws), ws.play(), set "⏸ Pause"
    □ transportLoad(url, label, false, 'TabName', { cardWs: ws })

□ Stop button click handler:
    □ ws.stop()
    □ transportStop()
    □ Set "▶ Play"

□ ws.on('finish'):
    □ Set "▶ Play"
    □ transportStop()

□ ws.on('timeupdate'):
    □ Update timeLabel with formatTime(time) / formatTime(dur)

□ Rewind button:
    □ ws.setTime(0)
```

For **transport-driven mode** (compose pattern), replace the play button
handler with `_claimTransport` + `transportLoad(url, label, true, source)`.

---

## How `transportLoad` works internally

Understanding this prevents the "it plays but the transport doesn't update"
class of bugs.

### Card-driven flow (opts.cardWs provided)

1. `_clearCardLink()` — unsubscribes previous card's event listeners
2. Sets label in transport header
3. `ws.load(url)` — loads waveform into transport (visual only, not for playback)
4. Sets `_activeCardWs = opts.cardWs`
5. Subscribes to card ws events:
   - `timeupdate` → syncs transport cursor position + time display
   - `play` / `pause` → syncs transport play/pause button icon
   - `finish` → calls `_clearCardLink()` + syncs button

**Key insight**: the transport ws loads the audio URL for waveform display, but
never plays it. The card ws is the audio source. This is why passing `cardWs`
is critical — without it, the transport has no link to the card's playback state.

### Transport-driven flow (no opts.cardWs, autoplay=true)

1. `_clearCardLink()` — clears any previous card link
2. Sets label in transport header
3. `ws.load(url)` — loads audio into transport ws
4. `ws.once('ready', () => ws.play())` — transport ws plays the audio itself

**Key insight**: compose cards never call `ws.play()` on their own WaveSurfer.
Their ws instances are display-only — cursors are synced from transport via
`transportOnTimeUpdate`. This is the opposite of card-driven mode.

---

## How `transportStop` works

```js
export function transportStop() {
  if (_activeCardWs) {
    _activeCardWs.stop();     // stop the card's playback
    _clearCardLink();          // unsubscribe card events
  }
  if (ws) { ws.stop(); }      // stop transport's own ws
  _syncPlayBtn();              // reset transport play button icon
}
```

Both paths are always executed. This means calling `transportStop()` is safe
regardless of which mode was active — it handles card-driven and transport-driven
cleanup in one call.

---

## Common failure modes and how to diagnose

### "Transport shows Now Playing but audio has stopped"
**Cause**: Card's stop button or finish handler doesn't call `transportStop()`.
**Diagnosis**: Check if the component's stop button handler and `ws.on('finish')`
both call `transportStop()`.

### "Transport play/pause button doesn't match card state"
**Cause**: Card-driven mode but `cardWs` was not passed to `transportLoad`, so
the transport has no event subscription to the card's play/pause state.
**Diagnosis**: Check the `transportLoad` call — 5th arg must be `{ cardWs: ws }`.

### "Clicking transport play does nothing"
**Cause**: `_activeCardWs` is null (no card registered) and transport ws has
no audio loaded.
**Diagnosis**: Check if `transportLoad` was called before the user clicked
transport play.

### "Playing in one tab, switching tabs, transport still shows old audio"
**This is correct behavior.** The transport persists across tab switches. The
label shows which tab produced the audio via the `source` parameter.

### "Card plays but transport waveform is flat/empty"
**Cause**: Transport `ws.load(url)` failed (bad URL, CORS, 404).
**Diagnosis**: Check browser console for wavesurfer load errors on the
transport container.

### "Two cards play simultaneously"
**Cause**: `stopOtherPlayers(ws)` not called before `ws.play()`.
**Diagnosis**: Check the play button handler calls the component's exclusive
playback function.

---

## Rules for editing playback code

1. **Never remove `transportStop()` from a stop button or finish handler** unless
   you are converting to transport-driven mode (compose pattern).

2. **Never remove `{ cardWs: ws }` from a `transportLoad` call** in card-driven
   components. This is the link between card and transport.

3. **Always import both `transportLoad` AND `transportStop`** from
   `audio-player.js` in any component with playable result cards.

4. **The enhance auto-load pattern** (`transportLoad(url, label, false, source)`)
   with no `cardWs` and `autoplay=false` just loads the waveform into the
   transport without playing. This is intentional — it pre-loads so the user
   can click transport play to hear the result.

5. **Compose is the only transport-driven component.** Do not convert other
   components to transport-driven unless there is a specific reason (e.g.,
   section pill seeking needs transport-level control).

6. **Test all four behaviors** after any change: play→transport shows label,
   stop→transport clears, finish→transport clears, exclusive playback works.
