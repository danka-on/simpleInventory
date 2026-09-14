# Warehouse note transcription and translation

Warehouse > Item Prep > Voice Notes now includes a manual **Analyze + translate**
button under each recording. OpenAI Whisper transcribes the original Lithuanian
(`language=lt`), then Claude Haiku translates that transcript into English. Both
texts appear underneath the player. The Lithuanian transcript is preserved exactly
as returned by the transcription service; Claude does not rewrite it.

Written warehouse notes, prep notes, prep status notes, and prep reasons each have
**Translate to English**. Non-English text is assumed to be Lithuanian, including
typing without diacritics. Already-English text is returned unchanged. The
original stays visible above the saved English translation. Written translations
use Claude directly and do not need OpenAI or a recording.

Voice notes have **Analyze again** after completion to apply updated recognition
guidance to existing recordings. This is an explicit paid rerun; reopening a note
does not rerun it. A failed rerun restores the previously saved text.

## Lithuanian warehouse vocabulary

Whisper receives a short Lithuanian spelling prompt. Claude receives vocabulary
and quantity guidance for both written notes and voice transcripts:

| Lithuanian (plain typing accepted) | English |
| --- | --- |
| trūksta / truksta | missing |
| šaukštas / saukstas | spoon |
| lėkštė / lekste | plate |
| puodelis | cup / mug |
| didelis / didelė | big / large |
| mažas / maža / mazas / maza | small |
| yra tik / ira tik | there is only / there are only |
| yra tik trys / ira tik trys | there are only three |
| yra tik N | only N present; preserve the exact count |

Voice-only guidance explains that "rust" can be a recognition error for "trūksta"
when the Lithuanian context describes missing pieces. It is not a global text
replacement: actual corrosion (rūdys, surūdijęs) stays rust. Written English text
does not receive that recognition correction. "Only three present" never implies
"three missing" or an invented expected set size.

Opening or reopening the modal only reads saved analysis. Completed analyses are
reused, including repeated POST requests. Translation failures preserve Lithuanian
and allow a translation-only retry. Results belong to the media ID and recording
fingerprint, not the base barcode. Replacing a recording invalidates old results.
Concurrent requests for the same recording are rejected; expired processing claims
are retryable after three minutes. At most two recordings process simultaneously.

## Server setup

Both `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are configured on the Pi as of
2026-09-13. OpenAI Whisper model access was checked successfully after the key was
installed. Keys live in `/opt/sweetshelves/.env`; restart `sweetshelves.service`
after changing them. Never put API keys in browser code or commits.
Without the required key the button reports the missing server setup before any
provider request. OpenAI receives the selected audio; Anthropic receives its text.

Supported recording extensions: webm, m4a, mp3, mp4, mpeg, mpga, wav. Files must
be nonempty, under 25 MB, and inside `static/items_prep`. No arbitrary URL fetches.

## Integration and storage

- `voice_note_routes.register(app, BASE_DIR)` supports both the modular app and
  Desktop monolith.
- `GET /api/warehouse/voice-notes/<media_id>/analysis` reads saved text.
- `POST` to the same URL performs manual analysis or returns the saved result.
- The lazy `voice_note_analysis` table in `bol.db` stores both texts, source
  fingerprint, completion timestamp, processing lease, and sanitized retry error.
- Prep media and inventory rows remain unchanged. Deleted recordings cannot
  retrieve an analysis; stale analysis rows may remain until database maintenance.
- `written_note_routes.py` registers through the voice-note feature for both app
  layouts. Its GET/POST route is
  `/api/warehouse/written-notes/<kind>/<note_id>/translation`, with source kinds
  `warehouse`, `prep-note`, `prep-reason`, and `prep-status-note`.
- `written_note_translations` in `bol.db` stores translations per source kind,
  row ID, and exact source-text/barcode fingerprint. POST checks the visible
  original against the current database note. Edited or deleted notes cannot
  retrieve stale English, and concurrent requests are guarded by a processing lease.

## Verification and deployment status

26 isolated Python tests and the extended browser fixture passed in both checkouts.
The browser checks manual voice and written requests, all four written sources,
reopening, safe rendering, original/English order, 390px layout, translation retries,
and explicit voice reanalysis. All 90 application scripts parsed.
The combined 38-test integration run retained four existing signature assertions
for `_amazon_resolve_asin_from_upc`, `_fba_local_amazon_listing`,
`_fba_local_amazon_listing_by_asin`, and `_fba_amazon_box_label_document`.
The route contract and voice-note checks passed. Those assertions were not relaxed.

Desktop source is synchronized. The extended feature was deployed on 2026-09-13
using patches against current Pi files, preserving other deployed features. The
service restarted and is active. The warehouse page, note JavaScript (hash checked),
and written translation GET for an existing note returned HTTP 200. No existing
inventory notes or recordings were sent to providers. Current code backups are in
`/tmp/sweetshelves-written-20260913/before`.

Two live Claude checks used synthetic text: missing large plate, only three small
spoons, only 7 cups, and actual English rust were translated correctly. A synthetic
voice transcript containing "Rust trijų šaukštų" became missing three spoons, while
"Puodelis surūdijęs" remained a rusty cup. These checks verify translation guidance,
not audio recognition quality; no recording was transcribed for this update.

The four deployed files are:
`voice_note_routes.py`, `written_note_routes.py`, `static/warehouse-voice-notes.js`,
and `templates/searchrack.html`. The existing routing registration is sufficient.
Never transfer databases when deploying this feature.

## Receiving name dictation (2026-09-14)

When `/barcode` or `/multibarcode` asks for an item name, the prompt leads with a
microphone button. The browser records a short clip (WebM, MP4 or OGG, under 5 MB),
stops about a second after the speaker pauses, and posts it to
`POST /api/warehouse/name-dictation` (registered by `voice_note_routes.register`).
The server sends it to OpenAI `gpt-4o-transcribe` in English with a product-label
hint, falls back to `whisper-1` only when the newer model itself is refused, and
returns the cleaned name. Silence transcripts such as "Thank you." are rejected.
The text fills the name field for the worker to check; nothing is saved until
**Confirm name**, and clips are not stored. A synthetic-speech check that day chose
gpt-4o-transcribe: it spelled "Zwilling Henckels" right in about 1.2 s, where
whisper-1 wrote "Henkel".

Items without a thumbnail then get a photo step (`static/warehouse-identity.js`):
any number of photos, shrunk to 1600 px in the browser. **Save** stores the first
as the thumbnail (`/api/items-prep/temp-item`) and all of them as prep photos
(`/api/items_prep/diagnostic/<upc>/photos`); **Skip** adds the item without photos.
Closing the prompt at either step does not add the item.
