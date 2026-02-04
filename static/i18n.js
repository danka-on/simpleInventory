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

  // ===== index.html =====
  list_manager: { en: 'List Manager', lt: 'Sąrašų tvarkyklė' },
  store_listing_helper: { en: 'Store Listing Helper', lt: 'Parduotuvės pagalbininkas' },
  store_helper: { en: 'Store Helper', lt: 'Pagalbininkas' },
  fb_listings: { en: 'FB Listings', lt: 'FB skelbimai' },
  fb_listings_title: { en: 'Facebook Marketplace Listings', lt: 'Facebook Marketplace skelbimai' },
  fb_listings_empty: { en: 'No Facebook listings yet.', lt: 'Dar nėra Facebook skelbimų.' },
  fb_listings_empty_help: { en: 'Check the Facebook checkbox on items to track them here.', lt: 'Pažymėkite Facebook langelį prie prekių, kad čia jas matytumėte.' },
  fb_listings_search_placeholder: { en: 'Search UPC or Title', lt: 'Ieškoti UPC arba pavadinimo' },
  fb_listings_update: { en: 'Update', lt: 'Atnaujinti' },
  fb_listings_unlist: { en: 'Unlist', lt: 'Nuimti' },
  fb_listings_no_matches: { en: 'No matches found.', lt: 'Nerasta atitikmenų.' },
  fb_listings_warehouse_stock: { en: 'Warehouse Stock', lt: 'Sandėlio likutis' },
  scan: { en: 'Scan', lt: 'Skenuoti' },
  no_warehouse: { en: 'No Warehouse', lt: 'Nėra sandėlyje' },
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
  listed_on: { en: 'Listed on', lt: 'Skelbta' },
  no_matching_upc: { en: 'No matching UPC in warehouse', lt: 'Nėra atitinkančio UPC sandėlyje' },
  finder: { en: 'Finder', lt: 'Ieškiklis' },
  same_upc_both_stores: { en: 'Same UPC listed on both eBay and Amazon', lt: 'Tas pats UPC skelbiamas ir eBay, ir Amazon' },
  ebay: { en: 'eBay', lt: 'eBay' },
  amazon: { en: 'Amazon', lt: 'Amazon' },
  listings_on: { en: 'Listings on', lt: 'Skelbimai parduotuvėje' },
  same_upc_listed: { en: 'Same UPC listed', lt: 'Tas pats UPC skelbiamas' },
  times_on: { en: 'times on', lt: 'kartų parduotuvėje' },
  oversold_by: { en: 'Oversold by', lt: 'Perparduota per' },
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
  days: { en: 'Days:', lt: 'Dienos:' },
  info: { en: 'Info', lt: 'Informacija' },
  loc: { en: 'Loc', lt: 'Vieta' },
  add_item_to_shelf: { en: 'Add Item to Shelf', lt: 'Pridėti prekę į lentyną' },
  search_warehouse: { en: 'Search Warehouse', lt: 'Ieškoti sandėlyje' },
  preperation: { en: 'Preperation', lt: 'Paruošimas' },
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
  lock_shelf_multi: { en: 'Scan Multiple Barcodes (Same Shelf)', lt: 'Skenuoti kelias prekes (ta pati lentyna)' },
  locked_multi: { en: 'Scan Multiple Barcodes (Same Shelf)', lt: 'Skenuoti kelias prekes (ta pati lentyna)' },
  locked: { en: 'LOCKED', lt: 'UŽRAKINTA' },
  single_item: { en: 'Single Item (SCAN QR CODE)', lt: 'Viena prekė (SKENUOKITE QR KODĄ)' },
  invalid_location_title: { en: 'Invalid Shelf Code', lt: 'Neteisingas lentynos kodas' },
  invalid_location_body: { en: 'This is not a valid shelf/location code. Please scan a shelf QR code.', lt: 'Tai nėra galiojantis lentynos/vietos kodas. Prašome nuskenuoti lentynos QR kodą.' },
  scan_or_enter_qr: { en: 'Scan or enter QR code', lt: 'Nuskenuokite arba įveskite QR kodą' },
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
  record_in_person_sales: { en: 'Record in-person sales', lt: 'Registruoti asmeninius pardavimus' },
  barcode_print_que: { en: 'Barcode Print Que', lt: 'Brūkšninių kodų spausdinimo eilė' },
  print_queued_barcodes: { en: 'Print queued barcodes', lt: 'Spausdinti eilėje esančius kodus' },
  bol_extract: { en: 'BOL Extract', lt: 'BOL išrašas' },
  extract_bol_data: { en: 'Extract BOL data from Excel files', lt: 'Išrašyti BOL duomenis iš Excel failų' },
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
  send: { en: 'Send', lt: 'Siųsti' },

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
}

// Create language toggle widget
function createLangToggle() {
  const lang = getLang();
  if (!document.getElementById('lang-toggle-style')) {
    const style = document.createElement('style');
    style.id = 'lang-toggle-style';
    style.textContent = `
      :root { --lang-toggle-offset: 96px; }
      .lang-safe-right { margin-right: var(--lang-toggle-offset, 96px) !important; }
      #lang-toggle { margin:0; }
      #lang-toggle button {
        margin:0 !important;
        padding:0 12px !important;
        min-height:26px !important;
        height:26px !important;
        font-size:12px !important;
        line-height:1 !important;
        border-radius:999px !important;
        appearance:none;
        -webkit-appearance:none;
        box-shadow:none !important;
      }
      #lang-toggle button:focus { outline: none; }
    `;
    document.head.appendChild(style);
  }
  const toggle = document.createElement('div');
  toggle.id = 'lang-toggle';
  toggle.style.cssText = 'position:fixed;top:10px;right:10px;z-index:99999;display:flex;align-items:center;gap:4px;padding:3px;border-radius:999px;background:rgba(255,255,255,0.96);border:1px solid #dfe6e9;box-shadow:0 6px 16px rgba(0,0,0,0.12);font-size:12px;font-weight:700;font-family:Segoe UI,Arial,sans-serif;margin:0;';

  const btnEn = document.createElement('button');
  btnEn.textContent = 'ENG';
  btnEn.id = 'lang-btn-en';
  btnEn.style.cssText = 'border:none;min-width:44px;height:26px;padding:0 12px;border-radius:999px;cursor:pointer;transition:all 0.2s;display:flex;align-items:center;justify-content:center;line-height:1;font-size:12px;font-weight:700;margin:0;';
  btnEn.onclick = () => setLang('en');

  const btnLt = document.createElement('button');
  btnLt.textContent = 'LT';
  btnLt.id = 'lang-btn-lt';
  btnLt.style.cssText = 'border:none;min-width:38px;height:26px;padding:0 12px;border-radius:999px;cursor:pointer;transition:all 0.2s;display:flex;align-items:center;justify-content:center;line-height:1;font-size:12px;font-weight:700;margin:0;';
  btnLt.onclick = () => setLang('lt');

  toggle.appendChild(btnEn);
  toggle.appendChild(btnLt);
  document.body.appendChild(toggle);
  updateToggleUI();
}

function updateToggleUI() {
  const lang = getLang();
  const btnEn = document.getElementById('lang-btn-en');
  const btnLt = document.getElementById('lang-btn-lt');
  if (!btnEn || !btnLt) return;
  if (lang === 'en') {
    btnEn.style.background = '#00b894';
    btnEn.style.color = '#fff';
    btnLt.style.background = '#fff';
    btnLt.style.color = '#333';
  } else {
    btnLt.style.background = '#00b894';
    btnLt.style.color = '#fff';
    btnEn.style.background = '#fff';
    btnEn.style.color = '#333';
  }
}

// Auto-init on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
  createLangToggle();
  applyTranslations();
});

// Expose globally
window.t = t;
window.setLang = setLang;
window.getLang = getLang;
window.applyTranslations = applyTranslations;
window.i18n = { t, setLang, getLang, applyTranslations, translations };

})();

