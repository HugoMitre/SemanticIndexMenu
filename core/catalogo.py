"""
core/catalogo.py
================
Candidatos de portales de gobiernos estatales de México.

Las direcciones siguen la convención `[entidad].gob.mx` documentada por el
gobierno federal, pero NO están verificadas una por una: los portales estatales
cambian de dominio con los cambios de administración y algunos usan abreviaturas
(`nl`, `slp`, `bcs`, `col`, `qroo`, `edomex`).

La aplicación las verifica al analizar: si una dirección no responde o no
entrega submenús, el portal se omite y queda anotado en la bitácora del análisis
con el motivo. Sustituye ahí las que fallen.
"""

CATALOGO: list[tuple[str, str, str]] = [
    ("AGS", "Aguascalientes",      "https://www.aguascalientes.gob.mx/"),
    ("BC",  "Baja California",     "https://www.bajacalifornia.gob.mx/"),
    ("BCS", "Baja California Sur", "https://www.bcs.gob.mx/"),
    ("CAM", "Campeche",            "https://www.campeche.gob.mx/"),
    ("CHP", "Chiapas",             "https://www.chiapas.gob.mx/"),
    ("CHH", "Chihuahua",           "https://www.chihuahua.gob.mx/"),
    ("CMX", "Ciudad de México",    "https://www.cdmx.gob.mx/"),
    ("COA", "Coahuila",            "https://www.coahuila.gob.mx/"),
    ("COL", "Colima",              "https://www.col.gob.mx/"),
    ("DUR", "Durango",             "https://www.durango.gob.mx/"),
    ("GUA", "Guanajuato",          "https://www.guanajuato.gob.mx/"),
    ("GRO", "Guerrero",            "https://guerrero.gob.mx/"),
    ("HID", "Hidalgo",             "https://www.hidalgo.gob.mx/"),
    ("JAL", "Jalisco",             "https://www.jalisco.gob.mx/"),
    ("MEX", "Estado de México",    "https://edomex.gob.mx/"),
    ("MIC", "Michoacán",           "https://michoacan.gob.mx/"),
    ("MOR", "Morelos",             "https://morelos.gob.mx/"),
    ("NAY", "Nayarit",             "https://www.nayarit.gob.mx/"),
    ("NL",  "Nuevo León",          "https://www.nl.gob.mx/"),
    ("OAX", "Oaxaca",              "https://www.oaxaca.gob.mx/"),
    ("PUE", "Puebla",              "https://puebla.gob.mx/"),
    ("QUE", "Querétaro",           "https://www.queretaro.gob.mx/"),
    ("ROO", "Quintana Roo",        "https://qroo.gob.mx/"),
    ("SLP", "San Luis Potosí",     "https://www.slp.gob.mx/"),
    ("SIN", "Sinaloa",             "https://www.sinaloa.gob.mx/"),
    ("SON", "Sonora",              "https://www.sonora.gob.mx/"),
    ("TAB", "Tabasco",             "https://tabasco.gob.mx/"),
    ("TAM", "Tamaulipas",          "https://www.tamaulipas.gob.mx/"),
    ("TLA", "Tlaxcala",            "https://www.tlaxcala.gob.mx/"),
    ("VER", "Veracruz",            "https://www.veracruz.gob.mx/"),
    ("YUC", "Yucatán",             "https://www.yucatan.gob.mx/"),
    ("ZAC", "Zacatecas",           "https://www.zacatecas.gob.mx/"),
]
