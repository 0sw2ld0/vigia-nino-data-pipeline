"""Constantes del pipeline. Todo lo que la app muestra como texto o umbral vive aquí."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEBRADAS_GEOJSON = ROOT / "data_static" / "peru_quebradas.geojson"
SCHEMA_PATH = ROOT / "contract" / "status_quebradas.schema.json"
OUTPUT_DIR = ROOT / "public" / "v1"

SCHEMA_VERSION = "1.0"
MIN_APP_VERSION = {"ios": "1.0.0", "android": "1.0.0"}

# Las apps muestran en gris una lectura más antigua que esto.
STALE_AFTER_MINUTES = 60

GOES_BUCKET = "noaa-goes19"
RRQPE_PRODUCT = "ABI-L2-RRQPEF"
GLM_PRODUCT = "GLM-L2-LCFA"
OBSERVATION_SOURCE = "NOAA GOES-19 ABI RRQPE"

# Un área sin al menos esta fracción de píxeles con DQF == 0 se publica como "sin dato".
MIN_GOOD_PIXEL_FRACTION = 0.5

# GLM publica un archivo cada 20 s (45 en 15 min). Con menos archivos el conteo de rayos
# subestimaría la tormenta, así que se publica como "sin dato".
GLM_WINDOW_MINUTES = 15
GLM_MIN_FILE_FRACTION = 0.8

# Open-Meteo: se exige casi toda la ventana de 72 h para publicar el acumulado.
RAIN_ACCUM_HOURS = 72
RAIN_ACCUM_MIN_HOURS = 66

# Umbrales PROVISIONALES por perfil regional. Cada quebrada del GeoJSON elige uno con
# `threshold_profile`. Por ahora todos tienen los mismos valores: hay que calibrarlos con
# eventos pasados antes de usarlos para avisos Push automáticos.
_PROVISIONAL_THRESHOLDS = {
    "red_rain_mmh": 20.0,
    "red_rain_with_lightning_mmh": 10.0,
    "red_lightning": 5,
    "yellow_rain_mmh": 5.0,
    "yellow_lightning": 1,
    "yellow_wet_rain_mmh": 2.0,
    "yellow_wet_accum_72h_mm": 30.0,
}

THRESHOLD_PROFILES = {
    "LIMA_OBSERVATION": dict(_PROVISIONAL_THRESHOLDS),
    # Quebradas pequeñas de la sierra de Lima (Chosica, Huaycoloro). En el backtest de Yaku
    # (marzo 2023) los días con huaicos reportados el satélite marcó solo 8-10 mm/h sobre Quirio,
    # así que el rojo de 20 mm/h probablemente es demasiado alto aquí.
    "SIERRA_LIMA": dict(_PROVISIONAL_THRESHOLDS),
    # Costa norte (Piura, La Libertad). En marzo 2023 la cuenca del río Piura llegó al tope del
    # producto (100 mm/h) y pasó el 23 % del mes en amarillo o rojo.
    "COSTA_NORTE": dict(_PROVISIONAL_THRESHOLDS),
}

GLOBAL_NOTICE = {
    "severity": "INFO",
    "title": "Versión de prueba",
    "body": "Los niveles todavía no están calibrados con eventos pasados. Sigue siempre los avisos oficiales de SENAMHI e INDECI.",
    "url": None,
}

OFFICIAL_LINKS = [
    {"title": "Avisos meteorológicos de SENAMHI", "url": "https://www.senamhi.gob.pe/?p=aviso-meteorologico"},
    {"title": "INDECI — Defensa Civil", "url": "https://www.gob.pe/indeci"},
]
