# Warehouse voice-note transcription and translation

Warehouse > Item Prep > Voice Notes now includes a manual **Analyze + translate**
button under each recording. OpenAI Whisper transcribes the original Lithuanian
(`language=lt`), then Claude Haiku translates that transcript into English. Both
texts appear underneath the player. The Lithuanian transcript is preserved exactly
as returned by the transcription service; Claude does not rewrite it.

Opening or reopening the modal only reads saved analysis. Completed analyses are
reused, including repeated POST requests. Translation failures preserve Lithuanian
and allow a translation-only retry. Results belong to the media ID and recording
fingerprint, not the base barcode. Replacing a recording invalidates old results.
Concurrent requests for the same recording are rejected; expired processing claims
are retryable after three minutes. At most two recordings process simultaneously.

## Server setup

The Pi already had `ANTHROPIC_API_KEY` on 2026-09-13. It did **not** have
`OPENAI_API_KEY`. Add the latter to `/opt/sweetshelves/.env` on the server, then
restart `sweetshelves.service`. Never put API keys in browser code or commits.
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

## Verification and deployment status

16 isolated Python tests and the browser fixture passed in both checkouts. The
browser checks manual requests, reopening, safe rendering, original/English order,
390px layout, and translation retries. All 90 application scripts parsed.
The combined 65-test integration run retained four existing signature assertions
for `_amazon_resolve_asin_from_upc`, `_fba_local_amazon_listing`,
`_fba_local_amazon_listing_by_asin`, and `_fba_amazon_box_label_document`.
The route contract and voice-note checks passed. Those assertions were not relaxed.

Desktop source is synchronized. After the user's explicit deployment approval,
the Pi was updated on 2026-09-13 using patches against its current files, preserving
other deployed features. The service restarted and is active. The warehouse page,
voice JavaScript (hash checked), and analysis GET for an existing recording all
returned HTTP 200. No analysis POST or paid provider request was made during
verification. Code backups are in `/tmp/sweetshelves-voice-20260913/before`.

`OPENAI_API_KEY` is still absent on the Pi. Actual transcription quality and
provider access remain unverified until that key is configured and a real
recording is analyzed manually.

The four deployed files are:
`voice_note_routes.py`, `static/warehouse-voice-notes.js`,
`sweetshelves/routing.py`, and `templates/searchrack.html`.
Never transfer databases when deploying this feature.
