# Vigía El Niño — Pipeline de datos

El piloto público está en [0sw2ld0/vigia-nino-data-pipeline](https://github.com/0sw2ld0/vigia-nino-data-pipeline). El workflow procesa **solo Lima**, actualmente **Quirio y Pisocancha (Tanta, Yauyos)** con áreas de muestreo provisionales, con `--region Lima`. Los cauces nacionales y las apps se conservan aparte. Este piloto no necesita claves de NOAA u Open-Meteo.

Endpoints de GitHub Pages:

- [`v1/status_quebradas.json`](https://0sw2ld0.github.io/vigia-nino-data-pipeline/v1/status_quebradas.json): contrato consumido por las apps.
- [`v1/run_report.json`](https://0sw2ld0.github.io/vigia-nino-data-pipeline/v1/run_report.json): duración, tamaño del JSON, barrido satelital y cobertura de fuentes.

El cron solicita una ejecución a los minutos 7, 22, 37 y 52; GitHub puede retrasarla y desactiva las programaciones de repositorios públicos tras 60 días sin actividad. No se garantiza puntualidad. El piloto consulta la página vigente y la capa WFS de avisos SENAMHI. Solo asocia un aviso a un tramo IGN si la fecha coincide con la vigencia verificada y al menos el 90 % del tramo está dentro de sectores del mismo nivel. Los polígonos oficiales son sectores de aviso: no son manchas de inundación ni cuencas de observación. La ausencia de una asociación no demuestra ausencia de peligro. No hay notificaciones push. El acumulado de Open-Meteo es información de modelo, no una medición de pluviómetro.

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

- **Polígonos provisionales.** Las 4 cajas de `data_static/peru_quebradas.geojson` (`geometry_status: PROVISIONAL`) son aproximadas; hay que reemplazarlas por las cabeceras de cuenca reales (ANA / INGEMMET).
- **Umbrales sin calibrar.** Cada quebrada usa un perfil regional (`threshold_profile` en el GeoJSON → `config.THRESHOLD_PROFILES`: `SIERRA_LIMA`, `COSTA_NORTE`). Por ahora los perfiles tienen los mismos valores; hay que calibrar cada uno con el backtest antes de activar avisos Push. Para agregar una región nueva, crea el perfil en `config.py` y asígnalo en el GeoJSON (si el perfil no existe, el pipeline no arranca).
- RRQPE subestima la lluvia cálida de la costa y los Andes: un verde significa "el satélite no ve lluvia fuerte", no "zona segura".


## Exportación desde el repositorio privado

`tools/export_public_pipeline.py`, en el repositorio de las apps, copia únicamente la lista permitida de fuentes, contratos, pruebas, backtest y documentación del pipeline a una carpeta nueva. No copia aplicaciones, cartografía nacional, entornos, credenciales ni historial del repositorio privado. El repositorio público tiene historial independiente. Después de modificar el pipeline en el monorepositorio privado, volver a exportar y publicar un commit de esos archivos en el repositorio público; no hay sincronización automática del código entre ambos.


## Aviso oficial y lluvia son independientes

`official_notice` es opcional y se publica por zona, separado de `status` (observación experimental de lluvia). Incluye fuente, nivel SENAMHI, vigencia UTC, ámbito administrativo y fracción del tramo cubierto. Si la página no permite verificar la vigencia, la capa está incompleta, su fecha no coincide o la fuente falla, se publica `null` y el informe registra la falta de verificación. Al vencer, no se conserva como activo. Las apps también verifican la vigencia por su cuenta, incluso si están sin conexión.

Prueba del 24/10/2026: el aviso N.º 297 está vigente de las 13:00 del 24 a las 13:00 del 25 de octubre (Perú). De sus 432 sectores en Lima, 202 tienen nivel naranja y 230 amarillo; estos conteos no equivalen a quebradas únicas. El tramo IGN 343130, llamado PISOCANCHA, queda completamente dentro de un sector naranja en Tanta, Yauyos. El boletín nombra sectores y distritos, no esa quebrada individualmente: la asociación es por intersección espacial. Los tramos locales de Quirio y Huaycoloro no intersectan esa capa. Es una comprobación fechada, no una afirmación permanente.

Pisocancha usa una caja de muestreo alrededor del tramo (margen de 0,015°), expresamente provisional. Ni esa caja ni el sector del aviso se presentan como área de afectación. Puede coexistir un aviso oficial naranja con una lectura satelital de cero: describen información y ventanas temporales distintas, y el aviso no se rebaja por la lectura.


### Respaldo fechado para la prueba

La conexión de GitHub Actions a `www.senamhi.gob.pe` presentó `ConnectTimeout` el 24/10/2026, aunque las fuentes respondieron desde el equipo de verificación. Para probar la presentación se conserva `data_static/senamhi_verified_snapshot.json.gz`, consultado a las 19:50 del 24/10/2026 (Perú), con los 432 sectores oficiales de Lima. Si la consulta directa falla, se usa únicamente mientras el aviso siga vigente y la consulta original tenga como máximo seis horas. No se renueva su hora al leerlo: no es una fuente viva ni una solución permanente al acceso desde GitHub. El informe publica `state: verified_snapshot` y la tarjeta informa que usa una copia con hora de consulta. Después del límite o del vencimiento, no se publica como activo. La automatización de avisos necesita resolver el acceso a SENAMHI o incorporar un canal oficial alternativo antes de operar de forma continua.


### Diagnóstico independiente de conectividad

El workflow manual `diagnose-senamhi.yml` consulta por separado el portal `www.senamhi.gob.pe`, el dominio sin www, las capacidades WFS y una muestra GeoJSON de IDESEP. Incluye una consulta de control a GitHub y un solo reintento IPv4, con tiempos acotados y validación TLS activa. No modifica los datos publicados ni sus vigencias.

En la [ejecución del 24/10/2026](https://github.com/0sw2ld0/vigia-nino-data-pipeline/actions/runs/40150293249), los ejecutores estándar Ubuntu, Windows y macOS alcanzaron el control (HTTP 200), pero no pudieron conectar a SENAMHI ni a IDESEP en ninguno de los dos intentos (curl 28, timeout antes de recibir HTTP). Desde el equipo local, www y ambos servicios IDESEP respondieron HTTP 200. Esto reproduce un problema de conectividad desde los ejecutores probados; no permite atribuir con certeza el origen a un bloqueo, georrestricción, firewall o falla de enrutamiento. Cambiar únicamente de sistema operativo o repetir el mismo intento no resolvió el problema.

Para comparar desde otro entorno:

```sh
python src/diagnose_sources.py --out /tmp/senamhi-connectivity.json
```

La siguiente prueba debe usar un entorno de red distinto que pueda consultar las fuentes oficiales. Un colector o intermediario pequeño en otra plataforma puede mantener el procesamiento geoespacial en Actions; antes de depender de él, comprobar conectividad desde esa plataforma. Otra posibilidad es un equipo propio encendido con tareas programadas. El respaldo fechado actual sigue siendo solo una prueba temporal.

### Colector temporal en macOS

`src/collect_senamhi.py` descarga el portal y la capa WFS de Lima, comprueba vigencia,
fecha de geometrías, integridad y asociación espacial antes de publicar. Sube únicamente
datos oficiales públicos al archivo `senamhi_verified_snapshot.json.gz` de la release
`senamhi-live`, mediante la sesión local de `gh`; no publica credenciales ni crea commits
por cada descarga. Si falla, conserva la hora original de la última copia válida.

El Mac realiza únicamente conexiones salientes HTTPS a SENAMHI y GitHub. **Actions no
se conecta al Mac**: descarga la copia desde GitHub Releases, mediante la URL pública
`https://github.com/0sw2ld0/vigia-nino-data-pipeline/releases/download/senamhi-live/senamhi_verified_snapshot.json.gz`.
El recolector no abre puertos, no ejecuta un servidor web, no configura túneles y no
modifica CuyScale ni la autoridad del Mac. La app móvil también consulta GitHub Pages.

Con `SENAMHI_COLLECTOR_URL` configurado, Actions intenta primero la copia del Mac y
solo intenta la fuente oficial si esa copia falla. Sin esa variable consulta directamente
SENAMHI. Se evita esperar el timeout conocido desde GitHub en cada ejecución.
La copia vence al superar seis horas de antigüedad o al vencer
el aviso. La app continúa consumiendo el mismo JSON de Pages. Una descarga nueva llega
a la app después del siguiente procesamiento y despliegue de Actions.

En el repositorio privado, `python3 tools/build_senamhi_app.py` prepara el panel de barra
de menú y `python3 tools/install_senamhi_agent.py` instala ambos componentes por usuario.
No requieren sudo. El runtime aislado reside en
`~/Library/Application Support/VigiaSenamhi`, los registros en
`~/Library/Logs/VigiaSenamhi` y el panel en `~/Applications/Vigia Recolector.app`.
No necesitan abrir puertos ni alterar la VPN.

El panel tiene ventana propia y aparece también en el Dock. Su icono utiliza Chiyo
con una nube de lluvia, siguiendo la identidad de CuyCleaner y CuyScale; la barra de
menú es un acceso adicional. Los fuentes y el arte del panel permanecen en el
repositorio privado y no se incluyen en la exportación pública del pipeline.

`launchd` ejecuta el recolector al cargarlo y cada 900 segundos mientras la sesión esté
iniciada y el Mac esté despierto y conectado. El bloqueo de pantalla no exige por sí
mismo detenerlo, pero el reposo y la ausencia de red impiden las consultas. No se fuerza
al equipo a permanecer despierto ni se despierta automáticamente. El panel se abre al
iniciar sesión; cerrarlo no detiene el recolector. Sus controles permiten recolectar
ahora, reiniciar, detener o activar. Detener deshabilita también el inicio de la tarea
hasta volver a activarla. No se necesitan permisos de administrador.

Diagnóstico:

```sh
launchctl print gui/$(id -u)/dev.cuycoders.vigia.senamhi
cat "$HOME/Library/Application Support/VigiaSenamhi/state/status.json"
```

Para desinstalar, detener primero el recolector desde el panel y cerrar el panel.
Descargar su agente con `launchctl bootout gui/$(id -u)/dev.cuycoders.vigia.senamhi.panel`
y retirar las dos entradas `dev.cuycoders.vigia.senamhi*.plist` de
`~/Library/LaunchAgents`, la app y las carpetas de runtime/registros indicadas arriba.

### Etapa inicial de cobertura regional: avisos de toda Lima

`status_lima.json` utiliza el mismo contrato que `status_quebradas.json`, con IDs IGN
estables `IGN-<objectid>` y `official_source_retrieved_at_utc`. Seleccionamos **12.462
tramos** del catálogo nacional que intersectan el departamento 15 (Lima), incluida Lima
Metropolitana, mediante el [límite administrativo de INGEMMET](https://geocatmin.ingemmet.gob.pe/arcgis/rest/services/SERV_CARTOGRAFIA_DEMARCACION_WGS84/MapServer/0).
La selección no incluye Callao como región; conserva la geometría completa de los tramos
que cruzan el límite. Un tramo no equivale a una quebrada física única.

`data_static/lima_channels.json.gz` conserva coordenadas e identificadores originales;
`lima-index.json` enumera IDs y sectores locales para que las apps reutilicen la cartografía
nacional ya instalada, sin duplicar geometrías en el paquete de la app. El exportador
reproducible está en `tools/cartography/export_lima.py` del repositorio privado. La copia
del límite y su hash quedan registrados junto al catálogo.

El cruce usa un índice espacial STRtree y exige que un mismo nivel oficial cubra al menos
el 90 % del tramo. La consulta del aviso 297-2026 asociaba **1.799 tramos: 977 naranja y
822 amarillo**; estos números corresponden a esa consulta, no son constantes futuras.
Los tramos sin asociación no son zonas seguras y los sectores del aviso no son áreas de
inundación. No se inventan mediciones para los 12.462 tramos: la lluvia continúa en las
dos áreas provisionales del piloto, en `status_quebradas.json`; el feed regional mantiene
las observaciones en null y el estado de lluvia UNKNOWN, incluso cuando hay aviso oficial.

En las apps: Mapa → **Avisos y cauces de toda Lima**. Permite buscar por nombre en toda la
selección, filtrar avisos vigentes, consultar la fuente y buscar cerca del GPS o del centro
del mapa dentro de 50 km aproximados a puntos del cauce. Renderiza hasta 100 resultados.
Los cauces asociados se colorean con el nivel del aviso. Sin red se conserva el último
feed, pero las asociaciones y colores se ocultan al superar seis horas desde la consulta
original o vencer el aviso; nunca se renueva esa fecha al abrir la app.

`run_report.json` registra duración total, descarga y cruce oficial, validación/serialización
del JSON regional, tamaño y conteos. Primera medición local: proceso completo **13,03 s**,
consulta de copia y cruce **1,313 s** (descarga **0,767 s**, cruce **0,543 s**), preparación
regional **0,788 s**, JSON **7.894.390 bytes**. Son mediciones de esa ejecución; Actions
tiene además preparación de entorno, pruebas y publicación. El recolector también registra
sus métricas en `state/status.json` y las muestra en el panel.

La [ejecución regional verificada](https://github.com/0sw2ld0/vigia-nino-data-pipeline/actions/runs/40154612607)
procesó el conjunto en **37,94 s**: consulta y cruce **1,311 s**, de los que el cruce fue
**1,026 s**, y generación/validación regional **2,05 s**. La descarga comprimida del JSON
regional transfirió **242.550 bytes** (aunque descomprimido son 7.894.390 bytes).
Con estas mediciones, el cruce permanece en Actions; no justifica trasladarlo al Mac.

### Piloto ampliado: lluvia y rayos de toda Lima

El feed regional ahora incluye observación satelital para los **12.462 tramos**, además
de los avisos oficiales. `lima.observation_areas` proyecta las líneas a UTM 18S, construye
una franja de **1.000 m** a cada lado y la devuelve a WGS84. Es un área experimental de
muestreo, no la cuenca alta del tramo ni un área de inundación. Las franjas no se dibujan
como zonas de riesgo y los umbrales siguen sin calibrar.

Se descarga un solo archivo RRQPE para toda la ejecución. Se decodifica una única ventana
regional y un índice espacial consulta qué centros de píxel están en cada polígono. Solo
se aceptan DQF=0 y valores finitos no negativos, con al menos 50 % de píxeles válidos; en
áreas subpíxel sin centros interiores se usa el centro más cercano. La franja nominal no
implica precisión de 1 km: el [producto NOAA tiene resolución nominal de 2 km en nadir](https://www.ospo.noaa.gov/resources/documents/PUG/GS%20Series%20416-R-PUG-L2%20Plus-0349%20Vol%205%20v3.0%20final.pdf)
y estima lluvia a partir de información satelital, no de un pluviómetro local.

Los archivos GLM de la ventana de 15 minutos también se descargan una sola vez y se
cuentan con un índice de polígonos. Un rayo puede pertenecer a varias franjas solapadas:
no se deben sumar los conteos de los tramos para obtener un total regional. Si falta
cobertura temporal suficiente, los conteos se publican en null. Ausencia de lectura
de lluvia produce UNKNOWN, nunca verde. Los avisos oficiales conservan su nivel aunque
el satélite estime cero lluvia.

El acumulado modelado de 72 horas continúa solo para las dos áreas originales, sin
extrapolarlo a otros tramos ni multiplicar consultas a Open-Meteo. En el feed regional
permanece en null. Las apps muestran esa limitación y permiten elegir **Avisos oficiales**
o **Lluvia estimada** para colorear los cauces, manteniendo visible el aviso oficial en
la ficha. Los colores de lluvia caducan con el tiempo del barrido (60 minutos); los del
aviso tienen su propia caducidad. No se representan franjas de observación como afectación.

Primera prueba local ampliada: **16,01 s** de proceso completo, con 1,553 s preparando
franjas, 4,562 s descargando/muestreando RRQPE y 7,354 s descargando/contando GLM. Ese
barrido produjo lluvia y rayos para los 12.462 tramos, sin lluvias importantes detectadas,
y conservó los 1.799 avisos asociados. Son resultados fechados, no condiciones permanentes.
`run_report.json` publica ahora `observation_timings_seconds`, conteos de tramos con datos
y distribución de niveles de lluvia, por separado de los niveles oficiales.

La [prueba publicada con lluvia regional](https://github.com/0sw2ld0/vigia-nino-data-pipeline/actions/runs/40155725397)
terminó correctamente: **40,91 s** de procesamiento y **102 s** desde la creación del
workflow hasta terminar el despliegue. Preparar franjas tomó 3,248 s, RRQPE 1,794 s y
GLM 31,965 s. Se verificaron 12.462 lecturas de lluvia y rayos, conservando los niveles
oficiales independientes. El JSON comprimido transfirió 262.139 bytes en la comprobación.
El tiempo dominante fue la descarga y lectura de GLM; se mantiene en Actions para que
la observación satelital continúe aunque el Mac duerma. No fue necesario moverla al Mac.


## Acumulado y contexto hidrográfico de Lima

El feed regional incluye 72 horas completas de precipitación modelada GFS vía Open-Meteo para los 12.462 tramos IGN. Comparte 64 muestras de una malla de 0,25° y conserva la fecha original de descarga en caché por hora UTC. El modelo (~13 km) y la malla (~28 km) no resuelven cada quebrada: el acumulado es contexto y no activa umbrales regionales sin calibración. Valores faltantes se publican null.

`watersheds_lima.geojson` publica 30 unidades oficiales ANA completas; 12.460 tramos se asocian por al menos 90 % de longitud dentro de una sola unidad. Es pertenencia espacial, no una microcuenca aportante delineada por elevación. `exposure_lima.geojson` incluye seis componentes de una única huella histórica documentada de Quirio, evento del 23/03/2015, informe INGEMMET A6680. No representa afectación futura ni cobertura total de Lima. Cada capa conserva procedencia y fecha; no se redistribuyen los PDF. Los metadatos oficiales consultados no declaran licencia explícita.

`calibration_report.json` mantiene INSUFFICIENT_EVIDENCE: faltan ventanas positivas y negativas verificadas de Lima. El evaluador evita compartir episodios entre entrenamiento y validación y nunca selecciona umbrales automáticamente. En iOS y Android, Mapa → Lluvia, avisos y cauces de toda Lima muestra estos contextos y permite activar las capas del tramo seleccionado.

## Avisos del piloto norte

`public/v1/status_norte.json` incorpora 8 179 tramos IGN únicos de Piura y Tumbes (6 807 y 1 409, con 37 compartidos). Los avisos se asocian espacialmente a sectores SENAMHI vigentes, sin afirmar activaciones confirmadas. La lluvia satelital, rayos, acumulados, cuencas y áreas de afectación del norte aún no se incorporan: los valores de observación son null y el estado de lluvia UNKNOWN. Una copia limitada a Lima no verifica la cobertura norteña. El recolector conserva un único snapshot actualizado, sin archivos históricos por ejecución.
