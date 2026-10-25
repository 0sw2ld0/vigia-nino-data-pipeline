# Vigía El Niño — Pipeline de datos

El piloto público está en [0sw2ld0/vigia-nino-data-pipeline](https://github.com/0sw2ld0/vigia-nino-data-pipeline). El workflow procesa **solo Lima**, actualmente el área provisional de **Quirio**, con `--region Lima`. Los cauces nacionales y las apps se conservan aparte. Este piloto no necesita claves de NOAA u Open-Meteo.

Endpoints de GitHub Pages:

- [`v1/status_quebradas.json`](https://0sw2ld0.github.io/vigia-nino-data-pipeline/v1/status_quebradas.json): contrato consumido por las apps.
- [`v1/run_report.json`](https://0sw2ld0.github.io/vigia-nino-data-pipeline/v1/run_report.json): duración, tamaño del JSON, barrido satelital y cobertura de fuentes.

El cron solicita una ejecución a los minutos 7, 22, 37 y 52; GitHub puede retrasarla y desactiva las programaciones de repositorios públicos tras 60 días sin actividad. No se garantiza puntualidad. Los avisos oficiales se ofrecen **solo como enlaces**; no hay ingestión de boletines ni notificaciones push. El acumulado de Open-Meteo es información de modelo, no una medición de pluviómetro.

Cada 15 minutos genera `v1/status_quebradas.json` (contrato en [`contract/`](contract/)) y lo publica en GitHub Pages para las apps iOS y Android.

## Fuentes

| Dato | Fuente | Si falla |
| :--- | :--- | :--- |
| Lluvia (mm/h) | GOES-19 `ABI-L2-RRQPEF`, solo píxeles con `DQF == 0`, dentro del polígono real | `null` → la quebrada sale **UNKNOWN** (gris) |
| Rayos (15 min) | GOES-19 `GLM-L2-LCFA`, ventana que cruza el cambio de hora | `null` si faltan archivos (un conteo parcial parecería calma) |
| Lluvia 72 h | Open-Meteo, lluvia horaria de exactamente las últimas 72 h | `null` |

Cada fuente falla por separado. Si GitHub Actions deja de correr, el archivo publicado envejece y las apps lo muestran como "Datos desactualizados" a los 60 minutos.

## Ejecutar en local

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python src/main_nowcast.py --region Lima  # escribe status_quebradas.json y run_report.json
```

## Calibrar con eventos pasados (backtest)

[`backtest/backtest_yaku.py`](backtest/backtest_yaku.py) aplica el mismo semáforo a los datos históricos de GOES-16 (ciclón Yaku, marzo 2023) y lo compara con eventos reales de [`backtest/events.csv`](backtest/events.csv).

```bash
# Prueba corta (1 día, ~2 min)
.venv/bin/python backtest/backtest_yaku.py --start 2023-03-15 --end 2023-03-16 --step-minutes 60
# Todo marzo cada 30 min (~1,500 archivos, ~2.7 GB; reanudable si se corta)
.venv/bin/python backtest/backtest_yaku.py --workers 16
```

Resultado en `backtest/out/report.md`: tiempo en cada nivel, barridos con más lluvia, anticipación ante cada evento y un barrido de umbrales (detección frente a horas de falsa alarma). `--lightning` también cuenta rayos, pero descarga unos 45 archivos más por barrido.

**Limitaciones:** las noticias rara vez dan la hora exacta (por eso `uncertainty_hours`); los desbordes de río ocurren horas o días después de la lluvia en la cuenca, así que la ventana de 6 h los subestima; y con pocos eventos el barrido de umbrales orienta, pero no es estadísticamente sólido.

## Publicar

Esta carpeta está pensada para ser su propio repositorio público (`vigia-nino-data-pipeline`); el workflow solo corre si `.github/` está en la raíz del repo.

1. Crear el repositorio y subir esta carpeta.
2. *Settings → Pages*: seleccionar GitHub Actions como origen (`build_type: workflow`).
3. Ejecutar el workflow a mano una vez (*Actions → Run workflow*).
4. Poner la URL resultante (`https://0sw2ld0.github.io/vigia-nino-data-pipeline/v1`) en las apps. El código ya apunta a ella tras activar este piloto.

## Pendiente antes de usarlo para alertas

- **Polígonos provisionales.** Las 3 cajas de `data_static/peru_quebradas.geojson` (`geometry_status: PROVISIONAL`) son aproximadas; hay que reemplazarlas por las cabeceras de cuenca reales (ANA / INGEMMET).
- **Umbrales sin calibrar.** Cada quebrada usa un perfil regional (`threshold_profile` en el GeoJSON → `config.THRESHOLD_PROFILES`: `SIERRA_LIMA`, `COSTA_NORTE`). Por ahora los perfiles tienen los mismos valores; hay que calibrar cada uno con el backtest antes de activar avisos Push. Para agregar una región nueva, crea el perfil en `config.py` y asígnalo en el GeoJSON (si el perfil no existe, el pipeline no arranca).
- RRQPE subestima la lluvia cálida de la costa y los Andes: un verde significa "el satélite no ve lluvia fuerte", no "zona segura".


## Exportación desde el repositorio privado

`tools/export_public_pipeline.py`, en el repositorio de las apps, copia únicamente la lista permitida de fuentes, contratos, pruebas, backtest y documentación del pipeline a una carpeta nueva. No copia aplicaciones, cartografía nacional, entornos, credenciales ni historial del repositorio privado. El repositorio público tiene historial independiente. Después de modificar el pipeline en el monorepositorio privado, volver a exportar y publicar un commit de esos archivos en el repositorio público; no hay sincronización automática del código entre ambos.
