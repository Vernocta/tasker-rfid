"""Every word on the screens, in both languages.

The warehouse is in Buenos Aires, so Spanish is the default; English is
there for anyone who needs it. One entry per phrase with both languages
on adjacent lines, so a missing translation is obvious at a glance — and
a test checks that neither language has a key the other lacks.

To change what a screen says, change it here. Nothing is compiled: the
server hands the current language's strings to the template, and the same
strings to the browser for the parts JavaScript writes.

Product and customer names are not here. Those come from seeds/skus.csv
and seeds/customers.csv, so they read exactly as you typed them.
"""

LANGUAGES = ("es", "en")
DEFAULT_LANGUAGE = "es"

LANGUAGE_NAMES = {"es": "Español", "en": "English"}

TEXT: dict[str, dict[str, str]] = {
    # -- Shell ---------------------------------------------------------
    "nav.stock":            {"es": "Stock",       "en": "Stock"},
    "nav.live":             {"es": "Lecturas",    "en": "Live reads"},
    "nav.dispatch":         {"es": "Muelle",      "en": "Dispatch"},
    "nav.anomalies":        {"es": "Anomalías",   "en": "Anomalies"},
    "nav.reports":          {"es": "Informes",    "en": "Reports"},
    "shell.loading":        {"es": "Cargando…",   "en": "Loading…"},
    "shell.updated":        {"es": "Actualizado", "en": "Updated"},
    "shell.offline":        {"es": "Sin conexión con el sistema — mostrando las últimas cifras recibidas",
                             "en": "Cannot reach the system — showing the last figures received"},

    # -- Stock ---------------------------------------------------------
    "stock.title":          {"es": "Stock disponible", "en": "Stock on hand"},
    "stock.lede":           {"es": "Cajas en el depósito ahora mismo. Una caja cuenta acá desde que se lee en el portal de entrada, y deja de contar en cuanto se despacha.",
                             "en": "Boxes in the warehouse right now. A box counts here once it has been read at the entrance portal, and stops counting the moment it is dispatched."},
    "stock.col.sku":        {"es": "SKU",             "en": "SKU"},
    "stock.col.product":    {"es": "Producto",        "en": "Product"},
    "stock.col.boxes":      {"es": "Cajas en stock",  "en": "Boxes in stock"},
    "stock.total":          {"es": "Total",           "en": "Total"},
    "stock.empty":          {"es": "No hay stock. Las cajas aparecen acá cuando se leen en el portal de entrada.",
                             "en": "Nothing is in stock. Boxes appear here once they have been read at the entrance portal."},

    # -- Live reads ----------------------------------------------------
    "live.title":           {"es": "Lecturas en vivo", "en": "Live read feed"},
    "live.lede":            {"es": "Las últimas 50 lecturas en los portales. Cada línea es una cosa pasando por un portal, no una lectura de radio: una sola caja produce un par de cientos de lecturas, que se juntan en la línea que ve acá.",
                             "en": "The last 50 reads at the portals. One row is one thing passing a portal, not one radio read — a single box produces a couple of hundred radio reads, which are collapsed into the one line you see here."},
    "live.newest_first":    {"es": "Más reciente arriba", "en": "Newest first"},
    "live.col.time":        {"es": "Hora",          "en": "Time"},
    "live.col.portal":      {"es": "Portal",        "en": "Portal"},
    "live.col.tag":         {"es": "Etiqueta",      "en": "Tag"},
    "live.col.identity":    {"es": "Identificado como", "en": "Identified as"},
    "live.col.reads":       {"es": "Lecturas",      "en": "Reads"},
    "live.leaving":         {"es": "saliendo",      "en": "leaving"},
    "live.entering":        {"es": "entrando",      "en": "coming in"},
    "live.no_direction":    {"es": "sentido no determinado", "en": "direction unclear"},
    "live.unknown_tag":     {"es": "Etiqueta desconocida — sin registrar",
                             "en": "Unknown tag — not registered"},
    "live.nothing_recorded":{"es": "sin contenido registrado", "en": "with nothing recorded in it"},
    "live.empty":           {"es": "Todavía no hay lecturas. Lo que pase por un portal aparece acá en unos segundos.",
                             "en": "No reads yet. Anything passing a portal appears here within a few seconds."},

    # -- Dispatch ------------------------------------------------------
    "dispatch.title":       {"es": "Muelle de carga", "en": "Dispatch control"},
    "dispatch.dock":        {"es": "MUELLE",          "en": "DOCK"},
    "dispatch.closed":      {"es": "CERRADO",         "en": "CLOSED"},
    "dispatch.closed_lede": {"es": "Nada puede salir. Elija el cliente para empezar a cargar.",
                             "en": "Nothing can leave. Choose the customer to start loading."},
    "dispatch.customer":    {"es": "CLIENTE",         "en": "CUSTOMER"},
    "dispatch.choose":      {"es": "Elegir cliente…", "en": "Choose a customer…"},
    "dispatch.order_ref":   {"es": "REFERENCIA DE PEDIDO", "en": "ORDER REFERENCE"},
    "dispatch.open_dock":   {"es": "ABRIR MUELLE",    "en": "OPEN THE DOCK"},
    "dispatch.close_dock":  {"es": "CERRAR MUELLE",   "en": "CLOSE THE DOCK"},
    "dispatch.open_for":    {"es": "MUELLE ABIERTO · CARGANDO PARA",
                             "en": "DOCK OPEN · LOADING FOR"},
    "dispatch.order":       {"es": "Pedido",          "en": "Order"},
    "dispatch.no_order":    {"es": "Sin referencia de pedido", "en": "No order reference"},
    "dispatch.boxes_loaded":{"es": "CAJAS CARGADAS",  "en": "BOXES LOADED"},
    "dispatch.last_reads":  {"es": "ÚLTIMAS LECTURAS", "en": "LAST READS"},
    # Whole sentences, not fragments: Spanish puts "hace" before the
    # duration and English puts "ago" after it, so a shared fragment cannot
    # be assembled in both.
    "dispatch.open_since":  {"es": "Abierto {time} · hace {ago}",
                             "en": "Opened {time} · {ago} ago"},
    "dispatch.last_read":   {"es": "Última lectura hace {ago}",
                             "en": "Last read {ago} ago"},
    "dispatch.no_reads_yet":{"es": "Sin lecturas todavía", "en": "No reads yet"},
    "time.seconds":         {"es": "{n} s",           "en": "{n} s"},
    "time.minutes":         {"es": "{n} min",         "en": "{n} min"},
    "time.hours":           {"es": "{n} h",           "en": "{n} h"},
    "dispatch.empty":       {"es": "Todavía no se cargó nada. Los contenedores aparecen acá al pasar por la salida.",
                             "en": "Nothing loaded yet. Containers appear here as they pass the exit."},
    "dispatch.confirm":     {"es": "¿Cerrar el muelle? No se puede cargar nada más para este cliente.",
                             "en": "Close the dock? Nothing more can be loaded for this customer."},
    "dispatch.pick_first":  {"es": "Elija un cliente antes de abrir el muelle.",
                             "en": "Choose a customer before opening the dock."},
    "dispatch.no_customers":{"es": "Sin clientes — agréguelos a seeds/customers.csv",
                             "en": "No customers — add them to seeds/customers.csv"},

    # -- Anomalies -----------------------------------------------------
    "anom.title":           {"es": "Anomalías",  "en": "Anomaly queue"},
    "anom.lede":            {"es": "Todo lo que el sistema no pudo resolver solo. Nada de esto cambió ninguna cifra de stock — para eso está la cola. Resuelva cada caso después de mirarlo, y corrija el estado del contenedor si hace falta.",
                             "en": "Everything the system could not work out on its own. Nothing here has changed any stock figure — that is the point of the queue. Resolve an item once you have looked into it, and set the container's status at the same time if it needs putting right."},
    "anom.col.when":        {"es": "Cuándo",     "en": "When"},
    "anom.col.what":        {"es": "Qué pasó",   "en": "What happened"},
    "anom.col.tag":         {"es": "Etiqueta",   "en": "Tag"},
    "anom.resolve":         {"es": "RESOLVER",   "en": "RESOLVE"},
    "anom.your_name":       {"es": "SU NOMBRE",  "en": "YOUR NAME"},
    "anom.what_you_did":    {"es": "QUÉ HIZO",   "en": "WHAT YOU DID"},
    "anom.note_hint":       {"es": "Recontamos el pasillo; se cambió la etiqueta.",
                             "en": "Recounted the aisle; tag replaced."},
    "anom.also_set":        {"es": "TAMBIÉN CAMBIAR EL ESTADO",  "en": "ALSO SET THE STATUS"},
    "anom.leave_status":    {"es": "No cambiar el estado", "en": "Leave the status alone"},
    "anom.correction_note": {"es": "Cambiar el estado acá queda registrado como corrección manual, con su nombre y lo que escribió arriba.",
                             "en": "Changing the status here is recorded as a manual correction, with your name and what you typed above."},
    "anom.save":            {"es": "GUARDAR Y RESOLVER", "en": "SAVE AND RESOLVE"},
    "anom.cancel":          {"es": "CANCELAR",    "en": "CANCEL"},
    "anom.name_required":   {"es": "Ponga su nombre, para que quede registrado quién decidió.",
                             "en": "Put your name in, so the record shows who decided."},
    "anom.empty":           {"es": "Nada para revisar. Lo que el sistema no pueda resolver solo aparece acá.",
                             "en": "Nothing to look at. Anything the system cannot work out on its own appears here."},

    # -- Reports -------------------------------------------------------
    "rep.title":            {"es": "Consumo por cliente", "en": "Consumption by customer"},
    "rep.lede":             {"es": "Cuántas cajas de cada producto se llevó cada cliente. Sólo aparecen las cargas con un cliente elegido en el muelle, y por eso hay que abrir el muelle antes de cargar.",
                             "en": "How many boxes of each product each customer has taken. Only loads with a customer selected at the dock appear here, which is why the dock has to be opened before anything is loaded."},
    "rep.from":             {"es": "DESDE",       "en": "FROM"},
    "rep.to":               {"es": "HASTA",       "en": "TO"},
    "rep.show":             {"es": "VER ESTAS FECHAS", "en": "SHOW THESE DATES"},
    "rep.last90":           {"es": "ÚLTIMOS 90 DÍAS", "en": "LAST 90 DAYS"},
    "rep.col.customer":     {"es": "Cliente",     "en": "Customer"},
    "rep.col.product":      {"es": "Producto",    "en": "Product"},
    "rep.col.boxes":        {"es": "Cajas",       "en": "Boxes"},
    "rep.total":            {"es": "Total",       "en": "Total"},
    "rep.period":           {"es": "Despachos del {from} al {to}.", "en": "Dispatches from {from} to {to}."},
    "rep.bad_range":        {"es": "La fecha Desde es posterior a la fecha Hasta.",
                             "en": "The From date is after the To date."},
    "rep.empty":            {"es": "No se despachó nada en este período. Las cargas aparecen acá una vez que se abre el muelle para un cliente y los contenedores pasan por la salida.",
                             "en": "Nothing was dispatched in this period. Loads appear here once the dock has been opened for a customer and containers have passed the exit."},

    # -- Container statuses, as words rather than database constants ----
    "status.REGISTERED":    {"es": "Registrado",  "en": "Registered"},
    "status.IN_STOCK":      {"es": "En stock",    "en": "In stock"},
    "status.DISPATCHED":    {"es": "Despachado",  "en": "Dispatched"},
    "status.opt.REGISTERED":{"es": "Registrado — todavía no ingresó al stock",
                             "en": "Registered — not yet booked into stock"},
    "status.opt.IN_STOCK":  {"es": "En stock — está en el depósito",
                             "en": "In stock — it is on the shelf"},
    "status.opt.DISPATCHED":{"es": "Despachado — salió del depósito",
                             "en": "Dispatched — it left the building"},

    # -- Anomaly kinds -------------------------------------------------
    "kind.UNKNOWN_TID":        {"es": "Etiqueta desconocida",   "en": "Unknown tag"},
    "kind.ILLEGAL_TRANSITION": {"es": "Movimiento no permitido", "en": "Movement not allowed"},
    "kind.NO_DIRECTION":       {"es": "Sentido no determinado",  "en": "Direction unclear"},
    "kind.NO_SESSION":         {"es": "Sin cliente seleccionado", "en": "No customer selected"},
    "kind.SHORT_PALLET":       {"es": "Pallet incompleto",       "en": "Pallet short of boxes"},
    "kind.COUNT_MISMATCH":     {"es": "Diferencia de inventario", "en": "Cycle count variance"},

    "why.UNKNOWN_TID":        {"es": "Se leyó una etiqueta que no está registrada a ningún contenedor.",
                               "en": "A tag was read that is not registered to any container."},
    "why.ILLEGAL_TRANSITION": {"es": "El movimiento no encaja con dónde estaba el contenedor. Alguien tiene que decidir qué pasó.",
                               "en": "The movement does not fit where the container was. Someone needs to decide what happened."},
    "why.NO_DIRECTION":       {"es": "Las barreras de la salida no pudieron decir para qué lado fue, así que no se movió nada.",
                               "en": "The exit beams could not tell which way it went, so nothing was moved."},
    "why.NO_SESSION":         {"es": "Se leyó algo en la salida sin un cliente elegido. No se despachó.",
                               "en": "Something was read at the exit with no customer selected. It was not dispatched."},
    "why.SHORT_PALLET":       {"es": "Se leyeron menos cajas de las que hay en el pallet.",
                               "en": "Fewer boxes were read than are on the pallet."},
    "why.COUNT_MISMATCH":     {"es": "Un conteo cíclico encontró algo distinto de lo que dicen los registros.",
                               "en": "A cycle count found something different from the records."},

    # -- Labels inside an anomaly's detail ------------------------------
    "detail.portal":             {"es": "Portal",              "en": "Portal"},
    "detail.direction":          {"es": "Sentido",             "en": "Direction"},
    "detail.status":             {"es": "Estado anterior",     "en": "Status was"},
    "detail.declared_children":  {"es": "Cajas en el pallet",  "en": "Boxes on the pallet"},
    "detail.children_read":      {"es": "Cajas leídas",        "en": "Boxes read"},
    "detail.missing":            {"es": "Faltan",              "en": "Missing"},
    "detail.variance":           {"es": "Diferencia",          "en": "Variance"},
    "detail.cycle_id":           {"es": "Conteo",              "en": "Cycle count"},
    "detail.last_portal":        {"es": "Visto por última vez en", "en": "Last seen at"},
    "detail.corrected_from":     {"es": "Corregido de",        "en": "Corrected from"},
    "detail.corrected_to":       {"es": "Corregido a",         "en": "Corrected to"},
    "detail.resolution_note":    {"es": "Nota",                "en": "Note"},
}


def normalise(language: str | None) -> str:
    """Fall back to the default rather than showing a half-translated screen."""
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


def strings_for(language: str) -> dict[str, str]:
    """Every phrase in one language, ready for a template or the browser."""
    language = normalise(language)
    return {key: value[language] for key, value in TEXT.items()}
