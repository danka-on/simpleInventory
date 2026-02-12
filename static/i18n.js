// i18n.js - Centralized English/Lithuanian translation system
(function() {
'use strict';

const translations = {
  // ===== Common / Shared =====
  back: { en: 'Back', lt: 'Atgal' },
  exit: { en: 'Exit', lt: 'Išeiti' },
  cancel: { en: 'Cancel', lt: 'Atšaukti' },
  save: { en: 'Save', lt: 'Išsaugoti' },
  delete: { en: 'Delete', lt: 'Ištrinti' },
  add: { en: 'Add', lt: 'Pridėti' },
  edit: { en: 'Edit', lt: 'Redaguoti' },
  close: { en: 'Close', lt: 'Uždaryti' },
  search: { en: 'Search', lt: 'Ieškoti' },
  loading: { en: 'Loading...', lt: 'Kraunama...' },
  confirm: { en: 'Confirm', lt: 'Patvirtinti' },
  refresh: { en: 'Refresh', lt: 'Atnaujinti' },
  print: { en: 'Print', lt: 'Spausdinti' },
  create: { en: 'Create', lt: 'Sukurti' },
  move: { en: 'Move', lt: 'Perkelti' },
  duplicate: { en: 'Duplicate', lt: 'Dublikuoti' },
  select_all: { en: 'Select All', lt: 'Pasirinkti viską' },
  yes: { en: 'Yes', lt: 'Taip' },
  no: { en: 'No', lt: 'Ne' },
  done: { en: 'Done', lt: 'Atlikta' },
  skip: { en: 'Skip', lt: 'Praleisti' },
  status: { en: 'Status', lt: 'Būsena' },
  actions: { en: 'Actions', lt: 'Veiksmai' },
  title: { en: 'Title', lt: 'Pavadinimas' },
  quantity: { en: 'Quantity', lt: 'Kiekis' },
  clear: { en: 'Clear', lt: 'Išvalyti' },
  send: { en: 'Send', lt: 'Siųsti' },
  date: { en: 'Date', lt: 'Data' },
  recipient: { en: 'Recipient', lt: 'Gavėjas' },
  sender: { en: 'Sender', lt: 'Siuntėjas' },
  subject: { en: 'Subject', lt: 'Tema' },
  message: { en: 'Message', lt: 'Žinutė' },
  reply: { en: 'Reply', lt: 'Atsakyti' },
  inbox: { en: 'Inbox', lt: 'Gauta' },
  sent: { en: 'Sent', lt: 'Išsiųsta' },
  unread: { en: 'Unread', lt: 'Neskaityta' },
  all_stores: { en: 'All Stores', lt: 'Visos parduotuvės' },
  all_messages: { en: 'All Messages', lt: 'Visos žinutės' },
  mail_box: { en: 'Mailbox', lt: 'Pašto dėžutė' },
  mail_center: { en: 'Mail Center', lt: 'Pašto centras' },
  mail_center_desc: { en: 'Centralized messages for all stores', lt: 'Centralizuotos žinutės visoms parduotuvėms' },
  mail_search_placeholder: { en: 'Search subject, body, sender, recipient', lt: 'Ieškoti temos, teksto, siuntėjo, gavėjo' },
  mail_queue: { en: 'Queued Messages', lt: 'Žinučių eilė' },
  select_message_to_view: { en: 'Select a message to view details', lt: 'Pasirinkite žinutę detaliam peržiūrėjimui' },
  send_message: { en: 'Send Message', lt: 'Siųsti žinutę' },
  recipient_placeholder: { en: 'Recipient name/email', lt: 'Gavėjo vardas/el. paštas' },
  subject_placeholder: { en: 'Subject', lt: 'Tema' },
  message_body: { en: 'Message Body', lt: 'Žinutės tekstas' },
  message_body_placeholder: { en: 'Type your message', lt: 'Įrašykite žinutę' },
  message_body_required: { en: 'Message body is required', lt: 'Žinutės tekstas privalomas' },
  message_sent_ok: { en: 'Message sent', lt: 'Žinutė išsiųsta' },
  no_messages_found: { en: 'No messages found', lt: 'Žinučių nerasta' },
  sync_live: { en: 'Sync Live', lt: 'Sinchronizuoti gyvai' },
  live_syncing: { en: 'Syncing live updates...', lt: 'Sinchronizuojami gyvi atnaujinimai...' },
  live_sync_ok: { en: 'Live sync completed', lt: 'Gyva sinchronizacija baigta' },

  // ===== index.html =====
  list_manager: { en: 'Prepared Items', lt: 'Paruoštos prekės' },
  store_manager: { en: 'Store Manager', lt: 'Parduotuvės valdymas' },
  price_master: { en: 'Price Master', lt: 'Kainų meistras' },
  label_master: { en: 'Label Master', lt: 'Etikečių meistras' },
  store_listing_helper: { en: 'Store Listing Helper', lt: 'Parduotuvės pagalbininkas' },
  store_helper: { en: 'Store Doctor', lt: 'Parduotuvės daktaras' },
  fb_listings: { en: 'FB Listings', lt: 'FB skelbimai' },
  fb_listings_title: { en: 'Facebook Marketplace Listings', lt: 'Facebook Marketplace skelbimai' },
  fb_listings_empty: { en: 'No Facebook listings yet.', lt: 'Dar nėra Facebook skelbimų.' },
  fb_listings_empty_help: { en: 'Check the Facebook checkbox on items to track them here.', lt: 'Pažymėkite Facebook langelį prie prekių, kad čia jas matytumėte.' },
  fb_listings_search_placeholder: { en: 'Search UPC or Title', lt: 'Ieškoti UPC arba pavadinimo' },
  fb_listings_update: { en: 'Update', lt: 'Atnaujinti' },
  fb_listings_unlist: { en: 'Unlist', lt: 'Nuimti' },
  fb_listings_no_matches: { en: 'No matches found.', lt: 'Nerasta atitikmenų.' },
  fb_listings_warehouse_stock: { en: 'Warehouse Stock', lt: 'Sandėlio likutis' },
  fb_listings_add: { en: 'Add Listing', lt: 'Pridėti skelbimą' },
  fb_listings_add_placeholder: { en: 'Enter UPC to add', lt: 'Įveskite UPC, kad pridėtumėte' },
  fb_listings_add_item: { en: '+ Add Item', lt: '+ Pridėti prekę' },
  fb_listings_scan_barcode: { en: 'Scan barcode', lt: 'Skenuokite brūkšninį kodą' },
  fb_listings_search_name: { en: 'Search by name', lt: 'Ieškoti pagal pavadinimą' },
  fb_listings_search: { en: 'Search', lt: 'Ieškoti' },
  fb_listings_search_empty: { en: 'No matches found.', lt: 'Nerasta atitikmenų.' },
  fb_listings_search_error: { en: 'Search failed', lt: 'Paieška nepavyko' },
  fb_listings_add_success: { en: 'Listing added', lt: 'Skelbimas pridėtas' },
  fb_listings_add_error: { en: 'Could not add listing', lt: 'Nepavyko pridėti skelbimo' },
  fb_listings_sync: { en: 'Sync Sold', lt: 'Sinchronizuoti pardavimus' },
  fb_listings_sync_done: { en: 'Sync done: {updated} updated, {skipped} skipped', lt: 'Sinchronizacija baigta: atnaujinta {updated}, praleista {skipped}' },
  fb_listings_sync_error: { en: 'Sync failed', lt: 'Sinchronizacija nepavyko' },
  fb_listings_log_title: { en: 'Changes Log', lt: 'Pakeitimų žurnalas' },
  fb_listings_log_open: { en: 'Open Log', lt: 'Atidaryti žurnalą' },
  fb_listings_log_search_placeholder: { en: 'Search by UPC, title, note...', lt: 'Ieškoti pagal UPC, pavadinimą, pastabą...' },
  fb_listings_log_sort_newest: { en: 'Date (newest first)', lt: 'Data (naujausi viršuje)' },
  fb_listings_log_sort_oldest: { en: 'Date (oldest first)', lt: 'Data (seniausi viršuje)' },
  fb_listings_log_action_all: { en: 'All Actions', lt: 'Visi veiksmai' },
  notes: { en: 'Notes', lt: 'Pastabos' },
  fb_listings_log_empty: { en: 'No log entries yet.', lt: 'Žurnalas tuščias.' },
  fb_listings_log_date: { en: 'Date', lt: 'Data' },
  fb_listings_log_action: { en: 'Action', lt: 'Veiksmas' },
  fb_listings_log_qty: { en: 'Qty', lt: 'Kiekis' },
  fb_listings_log_note: { en: 'Note', lt: 'Pastaba' },
  fb_listings_log_error: { en: 'Failed to load log', lt: 'Nepavyko įkelti žurnalo' },
  fb_listings_log_undone: { en: 'Undone', lt: 'Atšaukta' },
  fb_listings_log_undo_error: { en: 'Undo failed', lt: 'Atšaukimas nepavyko' },
  fb_log_auto_sold: { en: 'Auto sold sync', lt: 'Automatinis pardavimo sinchronizavimas' },
  fb_log_auto_sold_missing: { en: 'Auto sold (not listed)', lt: 'Automatinis pardavimas (nėra skelbimo)' },
  fb_log_manual_add: { en: 'Manual add', lt: 'Pridėta rankiniu būdu' },
  fb_log_manual_qty_change: { en: 'Manual qty change', lt: 'Kiekis pakeistas rankiniu būdu' },
  fb_log_manual_unlist: { en: 'Manual unlist', lt: 'Nuimta rankiniu būdu' },
  scan: { en: 'Scan', lt: 'Skenuoti' },
  no_warehouse: { en: 'No Warehouse', lt: 'Nėra sandėlyje' },
  quantity_alert: { en: 'Quantity Alert', lt: 'Kiekio įspėjimas' },
  cross_store: { en: 'Cross-Store', lt: 'Tarp parduotuvių' },
  same_store_dup: { en: 'Same-Store Dup', lt: 'Tos pačios dublikatai' },
  qty_mismatch: { en: 'Qty Mismatch', lt: 'Kiekio neatitikimas' },
  mark_fixed: { en: 'Mark Fixed', lt: 'Pažymėti ištaisyta' },
  acknowledge: { en: 'Acknowledge', lt: 'Patvirtinti' },
  multi_store: { en: 'Multi-Store', lt: 'Kelios parduotuvės' },
  single_store: { en: 'Single Store', lt: 'Viena parduotuvė' },
  warehouse: { en: 'Warehouse', lt: 'Sandėlis' },
  overage: { en: 'Overage', lt: 'Perteklius' },
  listing_total: { en: 'Listing total', lt: 'Skelbimų suma' },
  all_listings_have_stock: { en: 'All listings have warehouse stock', lt: 'Visi skelbimai turi atsargas sandėlyje' },
  no_cross_store_dups: { en: 'No cross-store duplicates', lt: 'Nėra dublikatų tarp parduotuvių' },
  no_same_store_dups: { en: 'No same-store duplicates', lt: 'Nėra dublikatų toje pačioje parduotuvėje' },
  all_quantities_match: { en: 'All quantities match', lt: 'Visi kiekiai sutampa' },
  no_quantity_alerts: { en: 'No quantity alerts', lt: 'Nėra kiekio įspėjimų' },
  listing_qty: { en: 'Listing qty', lt: 'Skelbimo kiekis' },
  exceeds_warehouse_by: { en: 'Exceeds warehouse by', lt: 'Viršija sandėlį per' },
  listed_on: { en: 'Listed on', lt: 'Skelbta' },
  no_matching_upc: { en: 'No matching UPC in warehouse', lt: 'Nėra atitinkančio UPC sandėlyje' },
  finder: { en: 'Finder', lt: 'Ieškiklis' },
  same_upc_both_stores: { en: 'Same UPC listed on both eBay and Amazon', lt: 'Tas pats UPC skelbiamas ir eBay, ir Amazon' },
  ebay: { en: 'eBay', lt: 'eBay' },
  amazon: { en: 'Amazon', lt: 'Amazon' },
  facebook: { en: 'Facebook', lt: 'Facebook' },
  listings_on: { en: 'Listings on', lt: 'Skelbimai parduotuvėje' },
  same_upc_listed: { en: 'Same UPC listed', lt: 'Tas pats UPC skelbiamas' },
  times_on: { en: 'times on', lt: 'kartų parduotuvėje' },
  oversold_by: { en: 'Oversold by', lt: 'Perparduota per' },
  pending_auto_removal_sales: { en: 'Pending auto-removal sales', lt: 'Laukiantys automatinio nurašymo pardavimai' },
  effective_after_sales: { en: 'Effective after sales', lt: 'Efektyvus kiekis po pardavimų' },
  no_effective_stock_after_sales: { en: 'No effective stock after pending sales', lt: 'Po laukiančių pardavimų efektyvių atsargų nėra' },
  listing: { en: 'Listing', lt: 'Skelbimas' },
  marked_fixed: { en: 'Marked Fixed', lt: 'Pažymėta ištaisyta' },
  no_marked_fixed: { en: 'No marked fixed items', lt: 'Nėra pažymėtų ištaisytų' },
  alert_type: { en: 'Type', lt: 'Tipas' },
  store: { en: 'Store', lt: 'Parduotuvė' },
  listings: { en: 'Listings', lt: 'Skelbimai' },
  dismissed_at: { en: 'Dismissed at', lt: 'Pažymėta' },
  scan_failed: { en: 'Scan failed', lt: 'Skenavimas nepavyko' },
  error_scanning: { en: 'Error scanning', lt: 'Klaida skenuojant' },
  failed_to_dismiss: { en: 'Failed to dismiss', lt: 'Nepavyko pažymėti' },
  error: { en: 'Error', lt: 'Klaida' },
  unknown: { en: 'Unknown', lt: 'Nežinoma' },
  untitled: { en: 'Untitled', lt: 'Be pavadinimo' },
  no_image: { en: 'No img', lt: 'Nėra foto' },
  scanning_listings: { en: 'Scanning listings...', lt: 'Skenuojami skelbimai...' },
  item_manager: { en: 'Item Manager', lt: 'Prekių tvarkyklė' },
  ready_to_ship: { en: 'Ready to Ship', lt: 'Paruošta siųsti' },
  completed: { en: 'Completed', lt: 'Užbaigta' },
  loading_orders: { en: 'Loading orders...', lt: 'Kraunami užsakymai...' },
  alerts: { en: 'Alerts', lt: 'Įspėjimai' },
  no_warehouse_alerts: { en: 'No Warehouse Alerts', lt: 'Įspėjimai: nėra sandėlyje' },
  no_stock_in_warehouse_alert: { en: 'No stock in warehouse alert', lt: 'Įspėjimas: nėra sandėlio likučio' },
  listing_quantity_exceeds_warehouse_alert: { en: 'Listing quantity exceeds warehouse alert', lt: 'Įspėjimas: skelbimo kiekis viršija sandėlį' },
  no_active_alerts: { en: 'No active alerts right now.', lt: 'Šiuo metu nėra aktyvių įspėjimų.' },
  no_no_warehouse_alerts: { en: 'No no-warehouse alerts right now.', lt: 'Šiuo metu nėra įspėjimų apie sandėlį.' },
  open_store_doctor: { en: 'Open Store Doctor', lt: 'Atidaryti Store Doctor' },
  active_alerts: { en: 'Active Alerts', lt: 'Aktyvūs įspėjimai' },
  days: { en: 'Days:', lt: 'Dienos:' },
  info: { en: 'Info', lt: 'Informacija' },
  loc: { en: 'Loc', lt: 'Vieta' },
  add_item_to_shelf: { en: 'Add Item', lt: 'Pridėti prekę' },
  search_warehouse: { en: 'Warehouse', lt: 'Sandėlis' },
  preparation: { en: 'Preparation', lt: 'Paruošimas' },
  all_search_label: { en: 'ALL', lt: 'VISI' },
  all_search_placeholder: { en: 'Search all…', lt: 'Ieškoti visų…' },
  tools: { en: 'Tools', lt: 'Įrankiai' },
  find: { en: 'Find', lt: 'Rasti' },
  undo: { en: 'Undo', lt: 'Atšaukti' },
  mark_done: { en: 'Done', lt: 'Atlikta' },

  // ===== position.html =====
  location: { en: 'Location', lt: 'Vieta' },
  location_step_title: { en: 'Location QR (step 1/2)', lt: 'Vieta QR (1/2 žingsnis)' },
  location_qr: { en: 'Location QR', lt: 'Vieta QR' },
  qr: { en: 'QR', lt: 'QR' },
  step_1_2: { en: '(step 1/2)', lt: '(1/2 žingsnis)' },
  restart_scanner: { en: 'Restart Scanner', lt: 'Paleisti skaitytuvą iš naujo' },
  lock_shelf_multi: { en: 'Multiple Items', lt: 'Kelios prekės' },
  locked_multi: { en: 'Multiple Items', lt: 'Kelios prekės' },
  locked: { en: 'LOCKED', lt: 'UŽRAKINTA' },
  single_item: { en: 'Single Item', lt: 'Viena prekė' },
  invalid_location_title: { en: 'Invalid Shelf Code', lt: 'Neteisingas lentynos kodas' },
  invalid_location_body: { en: 'This is not a valid shelf/location code. Please scan a shelf QR code.', lt: 'Tai nėra galiojantis lentynos/vietos kodas. Prašome nuskenuoti lentynos QR kodą.' },
  scan_or_enter_qr: { en: 'Scan or enter QR LOCATION code', lt: 'Nuskenuokite arba įveskite QR vietos kodą' },
  edit_mode_moving: { en: 'EDIT MODE: Moving', lt: 'REDAGAVIMO REŽIMAS: Perkeliama' },
  items: { en: 'items', lt: 'prekių' },
  move_items_location: { en: 'Move Items to Another Location', lt: 'Perkelti prekes į kitą vietą' },
  lock_shelf_info: { en: 'Scan several barcodes in a row for the SAME shelf. This is helpful when you have multiple items to add before moving to the next step.', lt: 'Nuskenuokite kelis brūkšninius kodus iš eilės TAI pačiai lentynai. Tai patogu, kai turite kelias prekes įdėti prieš pereidami prie kito žingsnio.' },
  move_items_location_info: { en: 'Move items from one shelf/location to another in bulk. Pick the start and destination shelves, then move items (full or partial quantities) before completing.', lt: 'Perkelkite prekes iš vienos lentynos/vietos į kitą vienu kartu. Pasirinkite pradžios ir paskirties lentynas, tada perkelkite prekes (pilnai arba dalimis) ir užbaikite.' },

  // ===== barcode.html =====
  barcode_step_title: { en: 'Barcode (step 2/2)', lt: 'Brūkšninis kodas (2/2 žingsnis)' },
  barcode_will_appear: { en: 'BARCODE will appear here', lt: 'Čia bus rodomas BRŪKŠNINIS KODAS' },
  scan_or_enter_barcode: { en: 'Scan or enter barcode', lt: 'Nuskenuokite arba įveskite brūkšninį kodą' },
  loading_video: { en: 'Loading video...', lt: 'Kraunamas vaizdas...' },
  scanned_barcodes: { en: 'Scanned Barcodes', lt: 'Nuskaityti brūkšniniai kodai' },
  confirm_continue: { en: 'Confirm & Continue', lt: 'Patvirtinti ir tęsti' },
  no_barcodes_scanned: { en: 'No barcodes scanned yet', lt: 'Dar nėra nuskaitytų brūkšninių kodų' },
  auto_submit_in: { en: 'Auto-submit in', lt: 'Automatinis pateikimas po' },
  scan_multiple: { en: 'Scan multiple barcodes...', lt: 'Skenuokite kelis brūkšninius kodus...' },
  invalid_barcode: { en: 'INVALID BARCODE', lt: 'NETINKAMAS BRŪKŠNINIS KODAS' },
  invalid_barcode_title: { en: 'Invalid Barcode', lt: 'Netinkamas brūkšninis kodas' },
  invalid_barcode_body: { en: 'This barcode format is not valid. Please scan a valid barcode.', lt: 'Šio brūkšninio kodo formatas neteisingas. Prašome nuskenuoti galiojantį brūkšninį kodą.' },
  uploading_picture: { en: 'Uploading picture...', lt: 'Įkeliamas paveikslėlis...' },
  restarting_camera: { en: 'Restarting camera...', lt: 'Paleidžiama kamera iš naujo...' },

  // ===== additem.html =====
  add_item_to_inventory: { en: 'Add Item to Inventory', lt: 'Pridėti prekę į inventorių' },
  add_item: { en: 'Add Item', lt: 'Pridėti prekę' },
  position_set: { en: 'Position set', lt: 'Pozicija nustatyta' },
  position_set_picture: { en: 'Position set - picture', lt: 'Pozicija nustatyta - nuotrauka' },
  position_not_set: { en: 'Position NOT set', lt: 'Pozicija NENUSTATYTA' },
  barcode_set: { en: 'Barcode set', lt: 'Brūkšninis kodas nustatytas' },
  barcodes_set: { en: 'Barcodes set', lt: 'Brūkšniniai kodai nustatyti' },
  barcode_not_set: { en: 'Barcode NOT set', lt: 'Brūkšninis kodas NENUSTATYTAS' },
  auto_adding_in: { en: 'Auto-adding in', lt: 'Automatinis pridėjimas po' },
  adding: { en: 'Adding...', lt: 'Pridedama...' },

  // ===== additem_multi.html =====
  item_added_success: { en: 'Item Added Successfully!', lt: 'Prekė sėkmingai pridėta!' },
  scan_next_item: { en: 'Scan Next Item', lt: 'Skenuoti kitą prekę' },
  multi_scan_active: { en: 'Multi-Scan Mode Active', lt: 'Aktyvus kelių skenavimų režimas' },
  returning_to_scan: { en: 'Returning to scan in', lt: 'Grįžtama į skenavimą po' },
  shelf: { en: 'Shelf:', lt: 'Lentyna:' },
  no_barcode: { en: 'No barcode', lt: 'Nėra brūkšninio kodo' },
  picture_position: { en: 'Picture position', lt: 'Nuotraukos pozicija' },
  location_not_set: { en: 'Location not set', lt: 'Vieta nenustatyta' },

  // ===== item_prep.html =====
  working_lot: { en: 'Working LOT:', lt: 'Darbinis LOT:' },
  loading_lots: { en: 'Loading lots...', lt: 'Kraunami LOT...' },
  no_lots_available: { en: 'No LOTs available', lt: 'Nėra galimų LOT' },
  initializing_camera: { en: 'Initializing camera...', lt: 'Inicializuojama kamera...' },
  ready_to_scan_upc: { en: 'Ready to scan UPC', lt: 'Paruošta skenuoti UPC' },
  enter_scan_upc: { en: 'Enter/scan UPC', lt: 'Įveskite/skenuokite UPC' },
  lookup: { en: 'Lookup', lt: 'Ieškoti' },
  no_barcode_btn: { en: 'No Barcode', lt: 'Be brūkšninio kodo' },
  create_new_item: { en: 'Create NEW Item', lt: 'Sukurti NAUJĄ prekę' },
  good: { en: 'Good', lt: 'Gera' },
  bad_diagnostic: { en: 'Bad → Diagnostic', lt: 'Bloga → Diagnostika' },
  return_item: { en: 'Return', lt: 'Grąžinimas' },
  undo_last_entry: { en: 'Undo Last Entry', lt: 'Atšaukti paskutinį įrašą' },
  print_que: { en: 'Print Que', lt: 'Spausdinimo eilė' },

  // ===== item_prep_create_item.html =====
  create_new_item_prep_title: { en: 'Create New Item - Prep', lt: 'Sukurti naują prekę - Paruošimas' },
  print_barcode_label: { en: 'Print Barcode Label', lt: 'Spausdinti brūkšninio kodo etiketę' },
  have_barcode: { en: 'Have Barcode', lt: 'Turiu brūkšninį kodą' },
  complete_add_to_print_que: { en: 'Complete & Add to Print Queue', lt: 'Užbaigti ir pridėti į spausdinimo eilę' },
  not_printed: { en: 'Not Printed', lt: 'Neatspausdinta' },
  printed_status: { en: 'Printed', lt: 'Atspausdinta' },
  captured: { en: 'Captured', lt: 'Nufotografuota' },

  // ===== item_prep_no_barcode.html =====
  item_prep_no_barcode_title: { en: 'Item Prep - No Barcode', lt: 'Prekių paruošimas - be brūkšninio kodo' },
  back_to_prep: { en: 'Back to Prep', lt: 'Atgal į paruošimą' },
  search_items_placeholder: { en: 'Search by item name or UPC code...', lt: 'Ieškoti pagal prekės pavadinimą arba UPC kodą...' },
  selected_item: { en: 'Selected Item', lt: 'Pasirinkta prekė' },
  print_barcode: { en: 'Print Barcode', lt: 'Spausdinti brūkšninį kodą' },
  add_to_print_que: { en: 'Add to Print Que', lt: 'Pridėti į spausdinimo eilę' },
  no_item_found_create_new: { en: 'No Item Found - Create New', lt: 'Prekė nerasta - sukurti naują' },
  prev: { en: 'Prev', lt: 'Ankstesnis' },
  next: { en: 'Next', lt: 'Kitas' },
  added: { en: 'Added!', lt: 'Pridėta!' },
  printing: { en: 'Printing...', lt: 'Spausdinama...' },
  printed: { en: 'Printed!', lt: 'Atspausdinta!' },
  print_dialog_opened: { en: 'Print Dialog Opened!', lt: 'Atidarytas spausdinimo langas!' },

  // ===== item_prep_create_item.html =====
  create_new_item_title: { en: 'Create New Item', lt: 'Sukurti naują prekę' },
  item_title_desc: { en: 'Item Title / Description', lt: 'Prekės pavadinimas / Aprašymas' },
  required: { en: 'Required', lt: 'Privaloma' },
  barcode_auto: { en: 'Barcode (Auto-Generated)', lt: 'Brūkšninis kodas (automatiškai sugeneruotas)' },
  click_to_edit: { en: 'Click to edit manually', lt: 'Spustelėkite, kad redaguotumėte rankiniu būdu' },
  generated: { en: 'Generated', lt: 'Sugeneruotas' },
  regenerate_barcode: { en: 'Regenerate Barcode', lt: 'Sugeneruoti brūkšninį kodą iš naujo' },
  thumbnail_image: { en: 'Thumbnail Image', lt: 'Miniatiūra' },
  start_camera: { en: 'Start Camera', lt: 'Paleisti kamerą' },
  enter_item_desc: { en: 'Enter item description...', lt: 'Įveskite prekės aprašymą...' },
  enter_barcode_manually: { en: 'Enter barcode manually...', lt: 'Įveskite brūkšninį kodą rankiniu būdu...' },

  // ===== item_prep_diagnostic.html =====
  diagnostic: { en: 'Diagnostic', lt: 'Diagnostika' },
  complete: { en: 'Complete', lt: 'Baigti' },

  // ===== items_to_list.html =====
  search_upc_title: { en: 'UPC or Title', lt: 'UPC arba pavadinimas' },
  all_lots: { en: 'All lots', lt: 'Visi LOT' },
  sort: { en: 'Sort:', lt: 'Rūšiuoti:' },

  // ===== tools.html =====
  marketplace_sale: { en: 'Marketplace Sale', lt: 'Pardavimas turgavietėje' },
  marketplace_session: { en: 'Marketplace Session', lt: 'Turgavietės sesija' },
  marketplace_scan_barcode: { en: 'Scan barcode...', lt: 'Skenuokite brūkšninį kodą...' },
  marketplace_submit_session: { en: 'Submit Session', lt: 'Pateikti sesiją' },
  marketplace_search_name: { en: 'Search by name', lt: 'Ieškoti pagal pavadinimą' },
  marketplace_empty: { en: 'Scan items to start a session.', lt: 'Skenuokite prekes, kad pradėtumėte sesiją.' },
  marketplace_total_qty: { en: 'Total Quantity', lt: 'Bendras kiekis' },
  marketplace_auto_total: { en: 'Auto Total', lt: 'Automatinė suma' },
  marketplace_final_total: { en: 'Final Total (optional override)', lt: 'Galutinė suma (nebūtina)' },
  marketplace_final_total_placeholder: { en: 'Leave blank to use auto total', lt: 'Palikite tuščią, kad naudotų automatinę sumą' },
  marketplace_item_name_optional: { en: 'Item name (optional)', lt: 'Prekės pavadinimas (nebūtina)' },
  marketplace_price_optional: { en: 'Price (optional)', lt: 'Kaina (nebūtina)' },
  marketplace_clear_confirm: { en: 'Clear all scanned items?', lt: 'Išvalyti visas nuskenuotas prekes?' },
  marketplace_submit_success: { en: 'Session submitted successfully.', lt: 'Sesija sėkmingai pateikta.' },
  marketplace_submit_partial: { en: 'Submitted with errors: {success} succeeded, {fail} failed.', lt: 'Pateikta su klaidomis: {success} pavyko, {fail} nepavyko.' },
  marketplace_search_error: { en: 'Search failed', lt: 'Paieška nepavyko' },
  marketplace_recent_sales: { en: 'Recent Sales', lt: 'Naujausi pardavimai' },
  marketplace_recent_empty: { en: 'No recent sales.', lt: 'Nėra naujų pardavimų.' },
  marketplace_recent_error: { en: 'Failed to load.', lt: 'Nepavyko įkelti.' },
  marketplace_add_manually: { en: '＋ Add Manually', lt: '＋ Pridėti rankiniu būdu' },
  marketplace_close_manual: { en: '✕ Close Manual', lt: '✕ Uždaryti rankinį' },
  marketplace_manual_title_placeholder: { en: 'Item name (required)', lt: 'Prekės pavadinimas (būtina)' },
  marketplace_manual_name_required: { en: 'Please enter an item name', lt: 'Įveskite prekės pavadinimą' },
  marketplace_manual_added: { en: 'Added: {title}', lt: 'Pridėta: {title}' },
  marketplace_not_found: { en: 'Item not in system — enter a name below', lt: 'Prekė nerasta sistemoje — įveskite pavadinimą žemiau' },
  image_label: { en: 'Image', lt: 'Nuotrauka' },
  upc_label: { en: 'UPC', lt: 'UPC' },
  record_in_person_sales: { en: 'Record in-person sales', lt: 'Registruoti asmeninius pardavimus' },
  barcode_print_que: { en: 'Barcode Print Que', lt: 'Brūkšninių kodų spausdinimo eilė' },
  print_queued_barcodes: { en: 'Print queued barcodes', lt: 'Spausdinti eilėje esančius kodus' },
  bol_extract: { en: 'BOL Extract', lt: 'BOL išrašas' },
  extract_bol_data: { en: 'Extract BOL data from Excel files', lt: 'Išrašyti BOL duomenis iš Excel failų' },
  listing_agent: { en: 'Listing Center', lt: 'Skelbimų centras' },
  listing_agent_desc: { en: 'Draft and publish listings from UPCs', lt: 'Kurti ir publikuoti skelbimus pagal UPC' },
  sync_manager: { en: 'Sync Manager', lt: 'Sinchronizavimo tvarkyklė' },
  manage_syncing: { en: 'Manage eBay & Amazon syncing', lt: 'Valdyti eBay ir Amazon sinchronizavimą' },
  statistics: { en: 'Statistics', lt: 'Statistika' },
  financial_analytics: { en: 'Financial Analytics', lt: 'Finansinė analizė' },
  view_profit_cost: { en: 'View profit & cost analysis', lt: 'Peržiūrėti pelno ir kaštų analizę' },
  marketplace_stats: { en: 'Marketplace Stats', lt: 'Turgavietės statistika' },
  view_sales_history: { en: 'View sales history', lt: 'Peržiūrėti pardavimų istoriją' },
  bol_stats: { en: 'BOL Stats', lt: 'BOL statistika' },
  view_lot_progress: { en: 'View LOT prep progress', lt: 'Peržiūrėti LOT paruošimo eigą' },
  returns: { en: 'Returns', lt: 'Grąžinimai' },
  manage_returns: { en: 'Manage Amazon & eBay returns', lt: 'Valdyti Amazon ir eBay grąžinimus' },
  management_tools: { en: 'Management Tools', lt: 'Valdymo įrankiai' },
  shelf_manager: { en: 'Shelf Manager', lt: 'Lentynų tvarkyklė' },
  create_manage_shelves: { en: 'Create and manage shelves', lt: 'Kurti ir valdyti lentynas' },
  trash_manager: { en: 'Trash Manager', lt: 'Šiukšliadėžės tvarkyklė' },
  restore_purge_photos: { en: 'Restore or purge diagnostic photos', lt: 'Atkurti arba ištrinti diagnostikos nuotraukas' },
  printer_settings: { en: 'Printer Settings', lt: 'Spausdintuvo nustatymai' },
  configure_printer: { en: 'Configure Bluetooth printer', lt: 'Konfigūruoti Bluetooth spausdintuvą' },
  removed_items: { en: 'Removed Items', lt: 'Pašalintos prekės' },
  view_removal_log: { en: 'View auto-removal log', lt: 'Peržiūrėti automatinio pašalinimo žurnalą' },
  test_sold_orders: { en: 'Test Sold Orders', lt: 'Testiniai parduoti užsakymai' },
  create_test_orders: { en: 'Create test orders for testing', lt: 'Sukurti testinius užsakymus testavimui' },
  emailer: { en: 'Emailer', lt: 'El. pašto siuntėjas' },
  configure_email_alerts: { en: 'Configure automated email alerts', lt: 'Konfigūruoti automatinius el. pašto pranešimus' },
  misc: { en: 'Misc', lt: 'Kita' },
  timer_workflow_cache: { en: 'Timer, workflow & cache settings', lt: 'Laikmačio, darbo eigos ir talpyklos nustatymai' },
  device_settings: { en: 'Device Settings', lt: 'Įrenginio nustatymai' },
  camera_mode: { en: 'Camera Mode', lt: 'Kameros režimas' },
  using_device_camera: { en: 'Using device camera', lt: 'Naudojama įrenginio kamera' },
  scanner_mode: { en: 'Scanner Mode', lt: 'Skaitytuvo režimas' },
  using_handheld_scanner: { en: 'Using handheld scanner', lt: 'Naudojamas rankinis skaitytuvas' },

  // ===== searchrack.html =====
  multi_db_search: { en: 'Multi-DB Search', lt: 'Paieška keliose DB' },
  database: { en: 'Database:', lt: 'Duomenų bazė:' },
  stores: { en: 'Stores:', lt: 'Parduotuvės:' },
  search_all_databases: { en: 'Search all databases...', lt: 'Ieškoti visose duomenų bazėse...' },

  // ===== barcode_print_que.html =====
  print_all: { en: 'Print All', lt: 'Spausdinti viską' },
  reset_que: { en: 'Reset Que', lt: 'Atstatyti eilę' },
  no_barcodes_in_que: { en: 'No barcodes in the print que', lt: 'Spausdinimo eilėje nėra brūkšninių kodų' },
  queue_auto_clear: { en: 'Queue will auto-clear in', lt: 'Eilė bus automatiškai išvalyta po' },
  cancel_auto_clear: { en: 'Cancel Auto-Clear', lt: 'Atšaukti automatinį valymą' },

  // ===== cleanup.html =====
  automatic_cleanup: { en: 'Automatic Cleanup', lt: 'Automatinis valymas' },
  back_to_search: { en: 'Back to Search', lt: 'Grįžti į paiešką' },
  pending_auto_removals: { en: 'Pending Automatic Inventory Removals', lt: 'Laukiantys automatiniai inventoriaus pašalinimai' },
  run_now: { en: 'Run Now', lt: 'Paleisti dabar' },
  pending_zero_qty: { en: 'Pending Zero-Quantity Deletions', lt: 'Laukiantys nulinio kiekio ištrinimai' },
  order: { en: 'Order', lt: 'Užsakymas' },
  barcode: { en: 'Barcode', lt: 'Brūkšninis kodas' },
  qty: { en: 'Qty', lt: 'Kiekis' },
  action: { en: 'Action', lt: 'Veiksmas' },

  // ===== emailer.html =====
  email_alerts: { en: 'Email Alerts', lt: 'El. pašto pranešimai' },
  add_alert: { en: 'Add Alert', lt: 'Pridėti pranešimą' },
  email_address: { en: 'Email Address', lt: 'El. pašto adresas' },
  alert_type: { en: 'Alert Type', lt: 'Pranešimo tipas' },
  app_health_stats: { en: 'App Health Stats', lt: 'Programos būklės statistika' },
  inventory_alert: { en: 'Inventory Alert', lt: 'Inventoriaus pranešimas' },
  save_all_settings: { en: 'Save All Settings', lt: 'Išsaugoti visus nustatymus' },

  // ===== extractor.html =====
  bol_excel_extractor: { en: 'BOL Excel Extractor', lt: 'BOL Excel išrašytojas' },
  upload_excel_file: { en: 'Upload Excel File:', lt: 'Įkelti Excel failą:' },
  date_of_purchase: { en: 'Date of Purchase:', lt: 'Pirkimo data:' },
  shipping_cost: { en: 'Shipping Cost ($):', lt: 'Siuntimo kaina ($):' },
  upload_sync_bol: { en: 'Upload & Sync to BOL', lt: 'Įkelti ir sinchronizuoti su BOL' },
  database_statistics: { en: 'Database Statistics', lt: 'Duomenų bazės statistika' },
  upload_history: { en: 'Upload History', lt: 'Įkėlimų istorija' },
  edit_lot_info: { en: 'Edit LOT Information', lt: 'Redaguoti LOT informaciją' },
  save_changes: { en: 'Save Changes', lt: 'Išsaugoti pakeitimus' },

  // ===== finder.html =====
  item_finder: { en: 'Item Finder', lt: 'Prekių ieškiklis' },
  search_by_title: { en: 'Search by title, barcode, UPC...', lt: 'Ieškoti pagal pavadinimą, brūkšninį kodą, UPC...' },
  warehouse_searchrack: { en: 'Warehouse (searchRack)', lt: 'Sandėlis (searchRack)' },
  macy_bol: { en: 'Macy BOL (rawbol)', lt: 'Macy BOL (rawbol)' },
  words: { en: 'Words:', lt: 'Žodžiai:' },
  assign: { en: 'Assign', lt: 'Priskirti' },
  search_to_find: { en: 'Search to find items', lt: 'Ieškokite, kad rastumėte prekes' },
  no_results_found: { en: 'No results found', lt: 'Rezultatų nerasta' },
  location_assigned: { en: 'Location assigned!', lt: 'Vieta priskirta!' },
  remove: { en: 'Remove', lt: 'Pašalinti' },
  remove_from_inventory: { en: 'Remove from Inventory?', lt: 'Pašalinti iš inventoriaus?' },
  reduce_qty_by_1: { en: 'This will reduce the quantity by 1.', lt: 'Kiekis bus sumažintas 1.' },
  yes_remove: { en: 'Yes, Remove', lt: 'Taip, pašalinti' },
  pick_position_to_remove: { en: 'Pick position to remove from', lt: 'Pasirinkite poziciją pašalinimui' },
  searching: { en: 'Searching...', lt: 'Ieškoma...' },
  error_searching: { en: 'Error searching', lt: 'Paieškos klaida' },
  removed_1_from_inventory: { en: 'Removed 1 from inventory', lt: 'Pašalinta 1 iš inventoriaus' },
  order_marked_processed: { en: 'order marked processed', lt: 'užsakymas pažymėtas apdorotu' },
  new_qty: { en: 'New qty', lt: 'Naujas kiekis' },
  removed: { en: 'REMOVED', lt: 'PAŠALINTA' },
  remove_failed: { en: 'Remove failed', lt: 'Pašalinimas nepavyko' },
  error_removing: { en: 'Error removing from inventory', lt: 'Klaida šalinant iš inventoriaus' },
  undo_failed: { en: 'Undo failed', lt: 'Atšaukimas nepavyko' },
  error_undoing: { en: 'Error undoing removal', lt: 'Klaida atšaukiant pašalinimą' },
  removal_undone: { en: 'Removal undone — qty restored to', lt: 'Pašalinimas atšauktas — kiekis atkurtas iki' },
  no_position: { en: 'No position', lt: 'Nėra pozicijos' },
  remove_1_from: { en: 'Remove 1 from', lt: 'Pašalinti 1 iš' },
  at_position: { en: 'at position', lt: 'pozicijoje' },
  current_qty: { en: 'Current qty', lt: 'Dabartinis kiekis' },
  remove_1_from_this: { en: 'Remove 1 from this item?', lt: 'Pašalinti 1 iš šios prekės?' },
  search_warehouse: { en: 'Search Warehouse', lt: 'Ieškoti sandėlyje' },

  // ===== pictureposition.html =====
  take_picture_location: { en: 'Take Picture of Location', lt: 'Nufotografuoti vietą' },
  capture: { en: 'Capture', lt: 'Fotografuoti' },
  retake: { en: 'Retake', lt: 'Perfotografuoti' },
  abort: { en: 'Abort', lt: 'Nutraukti' },
  uploading: { en: 'Uploading...', lt: 'Įkeliama...' },
  upload_complete: { en: 'Upload complete.', lt: 'Įkėlimas baigtas.' },
  upload_failed: { en: 'Upload failed. Please retry or retake.', lt: 'Įkėlimas nepavyko. Bandykite dar kartą.' },
  upload_aborted: { en: 'Upload aborted. You can retake.', lt: 'Įkėlimas nutrauktas. Galite perfotografuoti.' },
  upload_error: { en: 'Upload error. Please retry or retake.', lt: 'Įkėlimo klaida. Bandykite dar kartą.' },
  error_uploading_picture: { en: 'Error uploading picture. Please try again.', lt: 'Klaida įkeliant nuotrauką. Bandykite dar kartą.' },

  // ===== shelfmanager.html =====
  groups: { en: 'Groups', lt: 'Grupės' },
  print_qr: { en: 'Print QR', lt: 'Spausdinti QR' },
  select: { en: 'Select', lt: 'Pasirinkti' },
  add_new_shelf: { en: 'Add New Shelf', lt: 'Pridėti naują lentyną' },
  shelf_code: { en: 'Shelf Code', lt: 'Lentynos kodas' },
  save_shelf: { en: 'Save Shelf', lt: 'Išsaugoti lentyną' },
  take_photo: { en: 'Take Photo', lt: 'Fotografuoti' },
  replace_image: { en: 'Replace Image', lt: 'Pakeisti nuotrauką' },
  continue_btn: { en: 'Continue', lt: 'Tęsti' },
  redraw_box: { en: 'Redraw Box', lt: 'Perbraižyti rėmelį' },
  draw_rect_hint: { en: 'Drag to draw a rectangle around the shelf area', lt: 'Vilkite, kad nubrėžtumėte stačiakampį aplink lentynos zoną' },
  create_new_group: { en: 'Create New Group', lt: 'Sukurti naują grupę' },
  group_name: { en: 'Group Name', lt: 'Grupės pavadinimas' },
  move_to_group: { en: 'Move to Group', lt: 'Perkelti į grupę' },
  view_items: { en: 'View Items', lt: 'Peržiūrėti prekes' },
  clear_all_inventory: { en: 'Clear All Inventory', lt: 'Išvalyti visą inventorių' },
  shelves: { en: 'shelves', lt: 'lentynų' },

  // ===== bol_stats.html =====
  bol_statistics: { en: 'BOL Statistics', lt: 'BOL statistika' },
  match_sold_to_lots: { en: 'Match Sold Items to LOTs', lt: 'Susieti parduotas prekes su LOT' },
  back_to_tools: { en: 'Back to Tools', lt: 'Grįžti į įrankius' },
  back: { en: 'Back', lt: 'Atgal' },
  loading_statistics: { en: 'Loading statistics...', lt: 'Kraunama statistika...' },
  no_lot_data: { en: 'No LOT data available', lt: 'Nėra LOT duomenų' },

  // ===== financial_analytics.html =====
  financial_analytics_title: { en: 'Financial Analytics', lt: 'Finansinė analizė' },

  // ===== barcode_print_view.html =====
  no_print_data: { en: 'No print data found', lt: 'Nėra spausdinimo duomenų' },
};

// Get current language (default: en)
function getLang() {
  return localStorage.getItem('lang') || 'en';
}

// Set language
function setLang(lang) {
  localStorage.setItem('lang', lang);
  applyTranslations();
}

// Get translated string by key
function t(key) {
  const lang = getLang();
  const entry = translations[key];
  if (!entry) return key;
  return entry[lang] || entry.en || key;
}

let _readyLabelEl = null;
let _readyLabelResizeBound = false;
function updateReadyLabel(){
  if(!_readyLabelEl) return;
  const isMobile = window.innerWidth <= 640;
  const lang = getLang();
  const shortText = (lang === 'lt') ? 'Siųsti' : 'Ship';
  const fullText = t('ready_to_ship');
  _readyLabelEl.textContent = isMobile ? shortText : fullText;
}

// Apply translations to all [data-i18n] elements
function applyTranslations() {
  const lang = getLang();
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const entry = translations[key];
    if (!entry) return;
    const text = entry[lang] || entry.en;
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      el.placeholder = text;
    } else {
      // Preserve child elements (like <i> icons) by only replacing text nodes
      // If element has no child elements, just set textContent
      if (el.children.length === 0) {
        el.textContent = text;
      } else {
        // Find and replace text nodes only
        for (let node of el.childNodes) {
          if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
            node.textContent = ' ' + text + ' ';
            break;
          }
        }
      }
    }
  });
  // Update toggle button states
  updateToggleUI();
  updateReadyLabel();
}

// --------------------
// Theme (Day / Night)
// --------------------
const THEME_MODE_KEY = 'ss_theme_mode'; // 'auto' | 'day' | 'night'
const THEME_NIGHT_START_KEY = 'ss_theme_night_start'; // '19:00'
const THEME_DAY_START_KEY = 'ss_theme_day_start'; // '07:00'
const THEME_APPLIED_KEY = 'ss_theme_applied'; // internal (no need for UI to touch)

function _lsGet(key, fallback=null){
  try{
    const v = localStorage.getItem(key);
    return v === null ? fallback : v;
  }catch(e){
    return fallback;
  }
}

function _lsSet(key, val){
  try{
    if(val === null || typeof val === 'undefined'){
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, String(val));
    }
  }catch(e){}
}

function getThemeMode(){
  const m = (_lsGet(THEME_MODE_KEY, 'auto') || 'auto').toString().trim().toLowerCase();
  if(m === 'day' || m === 'night' || m === 'auto') return m;
  return 'auto';
}

function _parseHHMM(val, fallbackMinutes){
  const s = (val ?? '').toString().trim();
  const m = s.match(/^(\d{1,2}):(\d{2})$/);
  if(!m) return fallbackMinutes;
  const hh = Math.min(23, Math.max(0, parseInt(m[1], 10)));
  const mm = Math.min(59, Math.max(0, parseInt(m[2], 10)));
  return (hh * 60) + mm;
}

function _isNightNow(date, nightStartMin, dayStartMin){
  const nowMin = (date.getHours() * 60) + date.getMinutes();
  if(nightStartMin === dayStartMin) return true; // edge case: "always night"
  if(nightStartMin > dayStartMin){
    return nowMin >= nightStartMin || nowMin < dayStartMin; // wraps midnight
  }
  return nowMin >= nightStartMin && nowMin < dayStartMin; // doesn't wrap (unusual but supported)
}

function _computeAutoTheme(){
  const nightStartStr = _lsGet(THEME_NIGHT_START_KEY, '19:00');
  const dayStartStr = _lsGet(THEME_DAY_START_KEY, '07:00');
  const nightStartMin = _parseHHMM(nightStartStr, 19 * 60);
  const dayStartMin = _parseHHMM(dayStartStr, 7 * 60);
  const isNight = _isNightNow(new Date(), nightStartMin, dayStartMin);
  return isNight ? 'night' : 'day';
}

function getAppliedTheme(){
  const applied = (_lsGet(THEME_APPLIED_KEY, '') || '').toString().trim().toLowerCase();
  if(applied === 'day' || applied === 'night') return applied;
  return '';
}

function applyThemeFromStorage(){
  const mode = getThemeMode();
  const theme = mode === 'auto' ? _computeAutoTheme() : mode;

  const root = document.documentElement;
  root.classList.remove('ss-theme-day', 'ss-theme-night');
  root.classList.add(theme === 'night' ? 'ss-theme-night' : 'ss-theme-day');
  _lsSet(THEME_APPLIED_KEY, theme);
  updateToggleUI();
}

function setThemeMode(mode){
  const m = (mode || '').toString().trim().toLowerCase();
  if(m !== 'auto' && m !== 'day' && m !== 'night') return;
  _lsSet(THEME_MODE_KEY, m);
  applyThemeFromStorage();
}

function toggleThemeManual(){
  const cur = getAppliedTheme() || (getThemeMode() === 'auto' ? _computeAutoTheme() : getThemeMode());
  const next = (cur === 'night') ? 'day' : 'night';
  setThemeMode(next); // manual override (auto can be re-enabled in /misc)
}

let _themeAutoTimer = null;
function _startAutoThemeTimer(){
  if(_themeAutoTimer) return;
  _themeAutoTimer = setInterval(() => {
    if(getThemeMode() !== 'auto') return;
    applyThemeFromStorage();
  }, 60 * 1000);

  document.addEventListener('visibilitychange', () => {
    if(document.visibilityState === 'visible' && getThemeMode() === 'auto'){
      applyThemeFromStorage();
    }
  });
}

// --------------------
// App-Wide Top Banner
// --------------------
function ensureTopBannerStyles(){
  if(document.getElementById('ss-top-banner-style')) return;
  const style = document.createElement('style');
  style.id = 'ss-top-banner-style';
  style.textContent = `
    :root { --ss-banner-h: 44px; }

    html.ss-theme-day{
      color-scheme: light;
      --primary: #0984e3;
      --accent: #00b894;
      --bg: #f9f9f9;
      --card: #fff;
      --border: #e0e0e0;
      --radius: 14px;
      --success: #4caf50;
      --success-light: #e8f5e9;

      --ink: #0f172a;
      --ink-bright: #0b1220;
      --muted: #475569;
      --muted-dim: #64748b;
      --bg0: #f9f9f9;
      --bg1: #ffffff;
      --bg2: #f1f5f9;
      --card-hover: #f8fafc;
      --border-hover: rgba(0, 0, 0, 0.14);
      --shadow: 0 16px 40px rgba(0, 0, 0, 0.12);
      --shadow2: 0 2px 10px rgba(0, 0, 0, 0.08);
      --focus: 0 0 0 4px rgba(0, 184, 148, 0.22);

      --ss-banner-bg: rgba(255,255,255,0.92);
      --ss-banner-border: rgba(0,0,0,0.10);
      --ss-banner-ink: #0f172a;
      --ss-banner-ink-muted: rgba(15,23,42,0.72);
    }

    html.ss-theme-night{
      color-scheme: dark;
      --primary: #60a5fa;
      --accent: #10b981;
      --bg: #0f172a;
      --card: #1e293b;
      --border: rgba(255, 255, 255, 0.08);
      --radius: 14px;
      --success: #10b981;
      --success-light: rgba(16,185,129,0.12);

      --ink: #e2e8f0;
      --ink-bright: #f8fafc;
      --muted: #94a3b8;
      --muted-dim: #64748b;
      --bg0: #0f172a;
      --bg1: #1e293b;
      --bg2: #334155;
      --card-hover: #263445;
      --border-hover: rgba(255,255,255,0.14);
      --shadow: 0 16px 40px rgba(0,0,0,0.35);
      --shadow2: 0 2px 10px rgba(0,0,0,0.25);
      --focus: 0 0 0 4px rgba(16,185,129,0.25);

      --ss-banner-bg: rgba(15, 23, 42, 0.88);
      --ss-banner-border: rgba(255,255,255,0.08);
      --ss-banner-ink: #e2e8f0;
      --ss-banner-ink-muted: rgba(226,232,240,0.72);
    }

    html.ss-theme-night body{
      background: var(--bg0);
      color: var(--ink);
    }
    html.ss-theme-day body{
      background: var(--bg);
      color: #111827;
    }

    /* Make legacy templates readable in Night Mode (many hardcode dark text colors). */
    html.ss-theme-night h1,
    html.ss-theme-night h2,
    html.ss-theme-night h3,
    html.ss-theme-night h4,
    html.ss-theme-night h5,
    html.ss-theme-night h6{
      color: var(--ink);
    }
    html.ss-theme-night p,
    html.ss-theme-night label,
    html.ss-theme-night .desc,
    html.ss-theme-night .hint{
      color: var(--muted);
    }
    html.ss-theme-night table,
    html.ss-theme-night th,
    html.ss-theme-night td{
      color: var(--ink);
    }

    /* Tables: force a consistent dark surface (many templates hardcode white cell backgrounds). */
    html.ss-theme-night table{
      background: var(--card) !important;
      border-color: var(--border) !important;
    }
    html.ss-theme-night table th{
      background: var(--bg2) !important;
      color: var(--ink) !important;
      border-color: var(--border) !important;
    }
    html.ss-theme-night table td{
      background: var(--card) !important;
      color: var(--ink) !important;
      border-color: var(--border) !important;
    }
    html.ss-theme-night table tr:hover td{
      background: rgba(255,255,255,0.04) !important;
    }

    /* Index "Ready to Ship" tables explicitly set backgrounds per-cell. */
    html.ss-theme-night #sold-orders-table,
    html.ss-theme-night #completed-orders-table{
      border-color: var(--border) !important;
    }
    html.ss-theme-night #sold-orders-table th,
    html.ss-theme-night #sold-orders-table td,
    html.ss-theme-night #completed-orders-table th,
    html.ss-theme-night #completed-orders-table td{
      border-color: var(--border) !important;
    }
    html.ss-theme-night #sold-orders-table tr.handled td,
    html.ss-theme-night #sold-orders-table tr.handled{
      background: rgba(255,255,255,0.06) !important;
      color: var(--muted) !important;
    }

    /* Common overlays/panels that are hardcoded white in legacy templates. */
    html.ss-theme-night .modal .card,
    html.ss-theme-night .dropdown-panel,
    html.ss-theme-night .sold-popup{
      background: var(--card) !important;
      color: var(--ink) !important;
      border-color: rgba(255,255,255,0.14) !important;
    }
    html.ss-theme-night .sold-popup-close{
      color: var(--muted) !important;
    }
    html.ss-theme-night #results-summary{
      background: rgba(255,255,255,0.04) !important;
      color: var(--ink) !important;
      border: 1px solid var(--border) !important;
    }

    html.ss-has-top-banner body{
      padding-top: calc(var(--ss-banner-h) + var(--ss-body-pad-top, 0px));
    }

    /* Keep fixed back buttons below the banner */
    html.ss-has-top-banner .back-fixed{
      top: calc(var(--ss-banner-h) + 12px) !important;
    }

    #ss-top-banner{
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 100000;
      background: var(--ss-banner-bg);
      border-bottom: 1px solid var(--ss-banner-border);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
    }
    #ss-top-banner .ss-inner{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 8px 10px;
      font-family: 'Segoe UI', Arial, sans-serif;
      flex-wrap: wrap;
      position: relative;
    }

    #ss-top-banner .ss-left,
    #ss-top-banner .ss-center,
    #ss-top-banner .ss-right{
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }
    #ss-top-banner .ss-left{
      flex: 1 1 auto;
      min-width: 0;
    }

    .ss-seg{
      display: inline-flex;
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid var(--ss-banner-border);
      background: rgba(0,0,0,0.02);
    }
    html.ss-theme-night .ss-seg{ background: rgba(255,255,255,0.04); }

    .ss-seg button{
      appearance: none;
      -webkit-appearance: none;
      border: 0;
      margin: 0;
      padding: 0 12px;
      height: 28px;
      min-width: 44px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .3px;
      background: transparent;
      color: var(--ss-banner-ink-muted);
      transition: background .15s ease, color .15s ease, transform .1s ease;
    }
    .ss-seg button:hover{ transform: translateY(-1px); }
    .ss-seg button.active{
      background: var(--accent);
      color: #fff;
    }

    .ss-pill{
      appearance: none;
      -webkit-appearance: none;
      border: 1px solid var(--ss-banner-border);
      background: rgba(0,0,0,0.02);
      color: var(--ss-banner-ink);
      border-radius: 999px;
      height: 28px;
      padding: 0 12px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .2px;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: transform .1s ease, filter .15s ease;
      white-space: nowrap;
      text-decoration: none;
    }
    html.ss-theme-night .ss-pill{ background: rgba(255,255,255,0.04); }
    .ss-pill:hover{ transform: translateY(-1px); filter: brightness(1.05); }

    .ss-pill .ss-dot{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 0 3px rgba(16,185,129,0.18);
    }
    .ss-pill .ss-count{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 18px;
      height: 18px;
      padding: 0 6px;
      border-radius: 999px;
      background: var(--success);
      color: #fff;
      font-size: 10px;
      font-weight: 900;
      line-height: 18px;
      box-shadow: 0 0 0 3px rgba(16,185,129,0.18);
    }
    .ss-pill.ss-ready{
      padding: 0 10px;
      gap: 6px;
    }
    .ss-pill.ss-ready.is-zero .ss-count{
      background: var(--muted-dim);
      box-shadow: none;
    }
    .ss-pill.ss-alerts{
      padding: 0 10px;
      gap: 6px;
    }
    .ss-pill.ss-alerts .ss-count{
      display: none;
      background: #64748b;
      box-shadow: none;
    }
    .ss-pill.ss-alerts.has-alerts{
      background: #dc2626 !important;
      border-color: #dc2626 !important;
      color: #fff !important;
      box-shadow: 0 0 0 3px rgba(220,38,38,0.16);
    }
    .ss-pill.ss-alerts.has-alerts .ss-count{
      display: inline-flex;
      background: #fff;
      color: #dc2626;
      min-width: 18px;
      height: 18px;
      line-height: 18px;
      box-shadow: none;
    }

    .ss-theme-wrap{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid var(--ss-banner-border);
      background: rgba(0,0,0,0.02);
    }
    html.ss-theme-night .ss-theme-wrap{ background: rgba(255,255,255,0.04); }
    .ss-theme-wrap button{
      appearance: none;
      -webkit-appearance: none;
      border: 0;
      margin: 0;
      height: 28px;
      padding: 0 12px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .2px;
      background: transparent;
      color: var(--ss-banner-ink-muted);
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: background .15s ease, color .15s ease, transform .1s ease;
    }
    .ss-theme-wrap button:hover{ transform: translateY(-1px); }
    .ss-theme-wrap button.active{
      background: var(--accent);
      color: #fff;
    }

    .ss-icon{
      padding: 0 10px;
      font-size: 14px;
      line-height: 1;
    }
    .ss-icon.ss-home{
      padding: 0 16px;
    }
    .ss-icon svg{
      width: 16px;
      height: 16px;
      display: block;
      stroke: currentColor;
    }
    .ss-settings{
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }
    .ss-settings-toggle{
      display: none;
    }

    .ss-search{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--ss-banner-border);
      background: rgba(0,0,0,0.02);
      border-radius: 999px;
      height: 28px;
      padding: 0 8px;
      min-width: 0;
    }
    html.ss-theme-night .ss-search{ background: rgba(255,255,255,0.04); }
    .ss-search .ss-search-label{
      font-size: 11px;
      font-weight: 800;
      color: var(--ss-banner-ink-muted);
      letter-spacing: .2px;
    }
    .ss-search input{
      border: 0;
      outline: none;
      background: transparent;
      color: var(--ss-banner-ink);
      font-size: 12px;
      min-width: 80px;
      width: 160px;
      max-width: 38vw;
      padding: 0 6px !important;
      height: 22px !important;
      line-height: 22px !important;
    }
    .ss-search input::placeholder{ color: var(--ss-banner-ink-muted); }
    .ss-search button{
      display:none;
    }

    /* Override page-level input/button styles that break the banner search. */
    #ss-top-banner .ss-search input{
      border: 0 !important;
      box-shadow: none !important;
      background: transparent !important;
      border-radius: 0 !important;
      margin: 0 !important;
    }
    #ss-top-banner .ss-search button{ display:none; }

    @media (max-width: 720px){
      .ss-search input{ width: 110px; }
      .ss-search .ss-search-label{ display:none; }
    }
    @media (max-width: 640px){
      #ss-top-banner .ss-inner{ padding: 6px 8px; gap: 6px; }
      .ss-pill{ height: 26px; font-size: 11px; }
      .ss-pill.ss-ready{ padding: 0 8px; }
      .ss-pill.ss-ready .ss-count{ min-width: 16px; height: 16px; font-size: 9px; line-height: 16px; }
      .ss-theme-wrap button{ height: 26px; font-size: 11px; }
      .ss-seg button{ height: 26px; font-size: 11px; }
      .ss-search{ height: 26px; }
      .ss-search input{ width: 90px; }
      .ss-icon svg{ width: 14px; height: 14px; }
      .ss-pill.ss-alerts{ display: none !important; }

      #ss-top-banner .ss-left{
        flex: 1 1 auto;
        min-width: 0;
      }
      #ss-top-banner .ss-right{
        flex: 0 0 auto;
        margin-left: auto;
        justify-content: flex-end;
        flex-wrap: nowrap;
        gap: 6px;
      }
      .ss-settings{
        display: none;
        position: absolute;
        top: calc(100% + 6px);
        left: 0;
        right: 0;
        width: 100%;
        justify-content: flex-start;
        padding: 8px 8px 10px 8px;
        border-radius: 14px;
        border: 1px solid var(--ss-banner-border);
        background: var(--ss-banner-bg);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        box-shadow: 0 10px 26px rgba(0,0,0,0.18);
      }
      html.ss-settings-open .ss-settings{
        display: flex;
        flex-wrap: wrap;
      }
      .ss-settings-toggle{
        display: inline-flex;
      }
    }

    html.ss-has-top-banner .topbar,
    html.ss-has-top-banner .navbar,
    html.ss-has-top-banner .token-banner,
    html.ss-has-top-banner .testing-banner{
      top: var(--ss-banner-h) !important;
    }
    html.ss-has-top-banner thead{
      top: var(--ss-banner-h) !important;
    }
    html.ss-has-top-banner thead th{
      top: var(--ss-banner-h) !important;
    }
    html.ss-has-top-banner [style*="position: sticky"][style*="top: 0"],
    html.ss-has-top-banner [style*="position:sticky"][style*="top:0"]{
      top: var(--ss-banner-h) !important;
    }

    /* Listing Manager: make Status + Refresh buttons blue in Night Mode. */
    html.ss-theme-night #status-filter-btn,
    html.ss-theme-night #refresh-btn{
      background: #2563eb !important;
      border-color: rgba(37,99,235,0.55) !important;
      color: #ffffff !important;
    }
    html.ss-theme-night #status-filter-btn:hover,
    html.ss-theme-night #refresh-btn:hover{
      filter: brightness(1.05);
    }

    /* Index → Ready To Ship tables live in their own scroll panes.
       Keep sticky headers pinned to the pane top (not offset by the global banner). */
    html.ss-has-top-banner #sold-orders-table thead tr,
    html.ss-has-top-banner #completed-orders-table thead tr,
    html.ss-has-top-banner #sold-orders-table thead th,
    html.ss-has-top-banner #completed-orders-table thead th,
    html.ss-has-top-banner #sold-orders-table thead,
    html.ss-has-top-banner #completed-orders-table thead{
      top: 0 !important;
    }

    @media print{
      #ss-top-banner{ display:none !important; }
    }
  `;
  document.head.appendChild(style);
}

function createTopBanner(){
  if(document.getElementById('ss-top-banner')) return;
  ensureTopBannerStyles();

  // Capture the page's original body padding-top so we can safely add
  // banner space without breaking page-specific layouts.
  try{
    const pt = parseFloat(getComputedStyle(document.body).paddingTop || '0') || 0;
    document.documentElement.style.setProperty('--ss-body-pad-top', `${pt}px`);
  }catch(e){}

  const bar = document.createElement('div');
  bar.id = 'ss-top-banner';
  bar.setAttribute('aria-label', 'Top banner');

  const inner = document.createElement('div');
  inner.className = 'ss-inner';

  const left = document.createElement('div');
  left.className = 'ss-left';

  const right = document.createElement('div');
  right.className = 'ss-right';

  const langSeg = document.createElement('div');
  langSeg.className = 'ss-seg';

  const btnEn = document.createElement('button');
  btnEn.type = 'button';
  btnEn.id = 'lang-btn-en';
  btnEn.textContent = 'ENG';
  btnEn.onclick = () => setLang('en');

  const btnLt = document.createElement('button');
  btnLt.type = 'button';
  btnLt.id = 'lang-btn-lt';
  btnLt.textContent = 'LT';
  btnLt.onclick = () => setLang('lt');

  langSeg.appendChild(btnEn);
  langSeg.appendChild(btnLt);

  const themeWrap = document.createElement('div');
  themeWrap.className = 'ss-theme-wrap';

  const themeBtn = document.createElement('button');
  themeBtn.type = 'button';
  themeBtn.id = 'ss-theme-toggle';
  themeBtn.title = 'Toggle Day/Night';
  themeBtn.innerHTML = `<span class="ss-dot" aria-hidden="true"></span><span id="ss-theme-label">Theme</span>`;
  themeBtn.onclick = toggleThemeManual;

  const autoBtn = document.createElement('button');
  autoBtn.type = 'button';
  autoBtn.id = 'ss-theme-auto';
  autoBtn.title = 'Switch to automatic day/night based on time';
  autoBtn.textContent = 'Auto';
  autoBtn.onclick = () => {
    setThemeMode('auto');
    updateToggleUI();
  };

  themeWrap.appendChild(themeBtn);
  themeWrap.appendChild(autoBtn);

  const settingsPanel = document.createElement('div');
  settingsPanel.className = 'ss-settings';
  settingsPanel.appendChild(langSeg);
  settingsPanel.appendChild(themeWrap);

  const settingsBtn = document.createElement('button');
  settingsBtn.type = 'button';
  settingsBtn.className = 'ss-pill ss-icon ss-settings-toggle';
  settingsBtn.title = 'Settings';
  settingsBtn.setAttribute('aria-label', 'Settings');
  settingsBtn.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <line x1="4" y1="6" x2="20" y2="6"></line>
      <circle cx="9" cy="6" r="2"></circle>
      <line x1="4" y1="12" x2="20" y2="12"></line>
      <circle cx="15" cy="12" r="2"></circle>
      <line x1="4" y1="18" x2="20" y2="18"></line>
      <circle cx="7" cy="18" r="2"></circle>
    </svg>
  `;

  const homeBtn = document.createElement('a');
  homeBtn.className = 'ss-pill ss-icon ss-home';
  homeBtn.href = '/';
  homeBtn.title = 'Home';
  homeBtn.setAttribute('aria-label', 'Home');
  homeBtn.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M3 10.5L12 3l9 7.5"></path>
      <path d="M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10"></path>
    </svg>
  `;

  const toolsBtn = document.createElement('a');
  toolsBtn.className = 'ss-pill ss-icon';
  toolsBtn.href = '/tools';
  toolsBtn.title = 'Tools';
  toolsBtn.setAttribute('aria-label', 'Tools');
  toolsBtn.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3"></circle>
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
    </svg>
  `;

  const readyBtn = document.createElement('a');
  readyBtn.className = 'ss-pill ss-ready';
  readyBtn.href = '/ready-to-ship';
  readyBtn.title = 'Ready to Ship';
  readyBtn.setAttribute('aria-label', 'Ready to Ship');
  readyBtn.innerHTML = `
    <span class="ss-ready-label" data-i18n="ready_to_ship">Ready to Ship</span>
    <span class="ss-count" id="ss-ready-count">0</span>
  `;

  const alertsBtn = document.createElement('a');
  alertsBtn.className = 'ss-pill ss-alerts';
  alertsBtn.href = '/alerts';
  alertsBtn.title = 'Alerts';
  alertsBtn.setAttribute('aria-label', 'Alerts');
  alertsBtn.style.display = 'none';
  alertsBtn.innerHTML = `
    <span class="ss-alerts-label" data-i18n="alerts">Alerts</span>
    <span class="ss-count" id="ss-alerts-count">0</span>
  `;
  const searchWrap = document.createElement('div');
  searchWrap.className = 'ss-search';
  searchWrap.innerHTML = `
    <span class="ss-search-label" data-i18n="all_search_label">ALL</span>
    <input type="text" id="all-search-input" data-i18n="all_search_placeholder" placeholder="Search all…" />
  `;

  left.appendChild(homeBtn);
  left.appendChild(alertsBtn);
  left.appendChild(readyBtn);
  left.appendChild(searchWrap);

  right.appendChild(settingsPanel);
  right.appendChild(settingsBtn);
  right.appendChild(toolsBtn);

  inner.appendChild(left);
  inner.appendChild(right);
  bar.appendChild(inner);

  if(document.body.firstChild){
    document.body.insertBefore(bar, document.body.firstChild);
  } else {
    document.body.appendChild(bar);
  }

  const root = document.documentElement;
  root.classList.add('ss-has-top-banner');

  const measure = () => {
    try{
      const h = Math.ceil(bar.getBoundingClientRect().height || 44);
      root.style.setProperty('--ss-banner-h', `${h}px`);
    }catch(e){}
  };
  measure();
  let _resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(measure, 150);
  });

  updateToggleUI();
  _readyLabelEl = readyBtn.querySelector('.ss-ready-label');
  updateReadyLabel();
  if(!_readyLabelResizeBound){
    window.addEventListener('resize', updateReadyLabel);
    _readyLabelResizeBound = true;
  }

  settingsBtn.addEventListener('click', () => {
    root.classList.toggle('ss-settings-open');
    measure();
  });

  window.addEventListener('resize', () => {
    if(window.innerWidth > 640){
      root.classList.remove('ss-settings-open');
    }
  });

  try{
    const input = document.getElementById('all-search-input');
    const go = () => {
      const q = (input && input.value || '').trim();
      if(q) window.location.href = `/unified-search?q=${encodeURIComponent(q)}`;
    };
    if(input) input.addEventListener('keydown', (e) => { if(e.key === 'Enter') go(); });
  }catch(e){}

  // Ready-to-ship count badge (cached)
  if(!window.ssReadyCount){
    const countEl = readyBtn.querySelector('.ss-count');
    const state = { count: 0, ts: 0, inflight: false };

    const getDays = () => {
      try{
        const raw = localStorage.getItem('ss_ready_days');
        const val = parseInt(raw || '2', 10);
        return (val && val > 0) ? val : 2;
      }catch(e){ return 2; }
    };

    const setDays = (val) => {
      const days = (val && val > 0) ? parseInt(val, 10) : 2;
      try{ localStorage.setItem('ss_ready_days', String(days)); }catch(e){}
      return days;
    };

    const setCount = (val) => {
      const count = Number.isFinite(val) ? Math.max(0, Math.round(val)) : 0;
      state.count = count;
      if(countEl) countEl.textContent = String(count);
      readyBtn.classList.toggle('is-zero', count === 0);
      readyBtn.setAttribute('aria-label', `Ready to Ship (${count})`);
      try{
        localStorage.setItem('ss_ready_count', String(count));
        localStorage.setItem('ss_ready_count_ts', String(Date.now()));
      }catch(e){}
    };

    const refresh = async (force = false) => {
      const now = Date.now();
      if(!force && state.ts && (now - state.ts) < 60 * 1000) return;
      if(state.inflight) return;
      state.inflight = true;
      const days = getDays();
      try{
        const resp = await fetch(`/api/ready-to-ship/count?days=${days}`, { cache: 'no-store' });
        const data = await resp.json().catch(()=> ({}));
        if(resp.ok && data && data.success){
          setCount(parseInt(data.count, 10) || 0);
        }
      }catch(e){}
      finally{
        state.ts = Date.now();
        state.inflight = false;
      }
    };

    // Load cached count immediately for a fast render.
    try{
      const cached = parseInt(localStorage.getItem('ss_ready_count') || '0', 10);
      const cachedTs = parseInt(localStorage.getItem('ss_ready_count_ts') || '0', 10);
      if(cachedTs && (Date.now() - cachedTs) < 60 * 1000){
        setCount(cached);
        state.ts = cachedTs;
      } else {
        refresh(true);
      }
    }catch(e){ refresh(true); }

    setInterval(() => refresh(false), 60 * 1000);

    window.ssReadyCount = { set: setCount, refresh, setDays, getDays };
  }

  // Store-listing alert badge (no_warehouse + quantity_alert)
  if(!window.ssNoWarehouseAlerts){
    const countEl = alertsBtn.querySelector('.ss-count');
    const state = { count: 0, ts: 0, inflight: false };

    const setCount = (val) => {
      const count = Number.isFinite(val) ? Math.max(0, Math.round(val)) : 0;
      state.count = count;
      if(countEl){
        countEl.textContent = String(count);
        countEl.style.display = count > 0 ? 'inline-flex' : 'none';
      }
      alertsBtn.classList.toggle('has-alerts', count > 0);
      alertsBtn.style.display = count > 0 ? 'inline-flex' : 'none';
      alertsBtn.setAttribute('aria-label', `Alerts (${count})`);
      try{
        localStorage.setItem('ss_store_alert_count', String(count));
        localStorage.setItem('ss_store_alert_count_ts', String(Date.now()));
      }catch(e){}
    };

    const refresh = async (force = false) => {
      const now = Date.now();
      if(!force && state.ts && (now - state.ts) < 60 * 1000) return;
      if(state.inflight) return;
      state.inflight = true;
      try{
        const resp = await fetch('/api/listing-helper/scan', { cache: 'no-store' });
        const data = await resp.json().catch(()=> ({}));
        if(resp.ok && data && data.success){
          const counts = data.counts || {};
          const noWarehouse = parseInt(counts.no_warehouse, 10) || 0;
          const qtyAlert = parseInt(counts.quantity_alert, 10) || 0;
          setCount(noWarehouse + qtyAlert);
        }
      }catch(e){}
      finally{
        state.ts = Date.now();
        state.inflight = false;
      }
    };

    try{
      const cached = parseInt(localStorage.getItem('ss_store_alert_count') || localStorage.getItem('ss_no_warehouse_alert_count') || '0', 10);
      const cachedTs = parseInt(localStorage.getItem('ss_store_alert_count_ts') || localStorage.getItem('ss_no_warehouse_alert_count_ts') || '0', 10);
      if(cachedTs && (Date.now() - cachedTs) < 60 * 1000){
        setCount(cached);
        state.ts = cachedTs;
      } else {
        refresh(true);
      }
    }catch(e){ refresh(true); }

    setInterval(() => refresh(false), 60 * 1000);
    window.addEventListener('focus', () => refresh(true));

    window.ssNoWarehouseAlerts = { set: setCount, refresh };
    window.ssAlertsCount = window.ssNoWarehouseAlerts;
  }
}

function updateToggleUI() {
  const lang = getLang();
  const btnEn = document.getElementById('lang-btn-en');
  const btnLt = document.getElementById('lang-btn-lt');
  if (btnEn) btnEn.classList.toggle('active', lang === 'en');
  if (btnLt) btnLt.classList.toggle('active', lang === 'lt');

  const themeBtn = document.getElementById('ss-theme-toggle');
  const themeLabel = document.getElementById('ss-theme-label');
  const autoBtn = document.getElementById('ss-theme-auto');
  const mode = getThemeMode();
  const applied = getAppliedTheme() || (mode === 'auto' ? _computeAutoTheme() : mode);
  const isAuto = mode === 'auto';
  if(themeLabel){
    themeLabel.textContent = (applied === 'night' ? 'Night' : 'Day');
  }
  if(themeBtn){
    themeBtn.classList.toggle('active', !isAuto);
  }
  if(autoBtn){
    autoBtn.classList.toggle('active', isAuto);
  }
}

// Auto-init
function _i18nInit(){
  createTopBanner();
  applyTranslations();
}
if(document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', _i18nInit);
} else {
  _i18nInit();
}

// Expose globally
window.t = t;
window.setLang = setLang;
window.getLang = getLang;
window.applyTranslations = applyTranslations;
window.i18n = { t, setLang, getLang, applyTranslations, translations };
window.ssTheme = {
  getMode: getThemeMode,
  setMode: setThemeMode,
  apply: applyThemeFromStorage,
  getApplied: getAppliedTheme,
};

// Apply theme as early as possible to reduce "flash" between page loads.
applyThemeFromStorage();
_startAutoThemeTimer();

})();
