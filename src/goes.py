"""Lectura de GOES-19 desde s3://noaa-goes19 (lluvia RRQPE y rayos GLM).

Toda función devuelve `None` cuando no hay dato confiable. Nunca se reemplaza un dato ausente por cero.
"""

import io
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import shapely
import xarray as xr
from pyproj import Transformer
from shapely.geometry import box, Point
from shapely.strtree import STRtree

import config
from areas import Area

_START_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d_")


def start_time_from_key(key: str) -> datetime:
    """Hora de inicio del barrido según el nombre del archivo (`_sYYYYDDDHHMMSSt_`)."""
    match = _START_RE.search(key)
    if not match:
        raise ValueError(f"Nombre de archivo GOES inesperado: {key}")
    year, doy, hour, minute, second = (int(g) for g in match.groups())
    return datetime(year, 1, 1, hour, minute, second, tzinfo=timezone.utc) + timedelta(days=doy - 1)


def list_recent_keys(
    fs, product: str, now: datetime, hours_back: int, bucket: str = config.GOES_BUCKET
) -> list[str]:
    """Lista los archivos de la hora actual y de las `hours_back` anteriores, ordenados por hora de inicio."""
    keys: list[str] = []
    for h in range(hours_back, -1, -1):
        t = now - timedelta(hours=h)
        prefix = f"{bucket}/{product}/{t:%Y/%j/%H}/"
        try:
            keys.extend(fs.ls(prefix))
        except FileNotFoundError:
            continue
    return sorted(keys, key=start_time_from_key)


# ---------------------------------------------------------------------------
# Lluvia (ABI-L2-RRQPEF)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RainStats:
    max_mmh: float
    mean_mmh: float


@dataclass(frozen=True)
class RainResult:
    scan_time: datetime | None
    s3_key: str | None
    per_area: dict[str, RainStats | None]
    overlay: tuple[dict, bytes] | None = None


def area_rain_stats(
    rain: np.ndarray, dqf: np.ndarray, lon: np.ndarray, lat: np.ndarray, area: Area
) -> RainStats | None:
    """Estadísticas de lluvia dentro del polígono real (no del rectángulo envolvente).

    Solo cuentan los píxeles con DQF == 0. Si menos de la mitad de los píxeles del área son
    válidos, devuelve `None`.
    """
    inside = shapely.contains_xy(area.geometry, lon, lat)
    if not inside.any():
        # Polígono más pequeño que un píxel (~2 km): usa el píxel más cercano al centroide.
        dist = (lon - area.centroid_lon) ** 2 + (lat - area.centroid_lat) ** 2
        inside = np.zeros_like(dist, dtype=bool)
        inside.flat[np.nanargmin(dist)] = True

    values = rain[inside]
    good = (dqf[inside] == 0) & np.isfinite(values) & (values >= 0)
    if good.sum() / inside.sum() < config.MIN_GOOD_PIXEL_FRACTION:
        return None
    valid = values[good]
    return RainStats(max_mmh=round(float(valid.max()), 1), mean_mmh=round(float(valid.mean()), 1))


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(microsecond=0)


def _geos_transformer(ds: xr.Dataset) -> tuple[Transformer, float]:
    proj = ds["goes_imager_projection"].attrs
    h = float(proj["perspective_point_height"])
    crs = (
        f"+proj=geos +h={h} +lon_0={float(proj['longitude_of_projection_origin'])} "
        f"+sweep={proj['sweep_angle_axis']} +a={float(proj['semi_major_axis'])} "
        f"+b={float(proj['semi_minor_axis'])} +units=m +no_defs"
    )
    return Transformer.from_crs("EPSG:4326", crs, always_xy=True), h


def _window(ds: xr.Dataset, transformer: Transformer, h: float, area: Area, pad: int = 2):
    """Índices (filas, columnas) del recorte que cubre el polígono, con margen de `pad` píxeles."""
    minx, miny, maxx, maxy = area.geometry.bounds
    lons = np.array([minx, maxx, minx, maxx])
    lats = np.array([miny, miny, maxy, maxy])
    gx, gy = transformer.transform(lons, lats)
    gx, gy = np.asarray(gx) / h, np.asarray(gy) / h  # metros -> radianes de barrido
    if not np.all(np.isfinite(gx)) or not np.all(np.isfinite(gy)):
        raise ValueError(f"{area.id} está fuera del disco visible de GOES-19")

    x = ds["x"].values  # ascendente
    y = ds["y"].values  # descendente (norte -> sur)
    c0 = max(int(np.searchsorted(x, gx.min())) - pad, 0)
    c1 = min(int(np.searchsorted(x, gx.max())) + pad, x.size)
    r0 = max(int(np.searchsorted(-y, -gy.max())) - pad, 0)
    r1 = min(int(np.searchsorted(-y, -gy.min())) + pad, y.size)
    return slice(r0, r1), slice(c0, c1)


def read_rrqpe(fs, areas: list[Area], now: datetime, bucket: str = config.GOES_BUCKET) -> RainResult:
    """Lee el RRQPE más reciente (busca en las 2 horas anteriores para no perder el cambio de hora)."""
    keys = [k for k in list_recent_keys(fs, config.RRQPE_PRODUCT, now, 2, bucket) if start_time_from_key(k) <= now]
    if not keys:
        return RainResult(scan_time=None, s3_key=None, per_area={a.id: None for a in areas})
    return rain_from_key(fs, keys[-1], areas)


def rain_from_key(fs, key: str, areas: list[Area]) -> RainResult:
    """Estadísticas de lluvia por área de un archivo RRQPE concreto (también para datos históricos)."""
    ds = xr.open_dataset(io.BytesIO(fs.cat(key)), engine="h5netcdf")
    try:
        transformer, h = _geos_transformer(ds)
        inverse = Transformer.from_crs(transformer.target_crs, "EPSG:4326", always_xy=True)
        per_area: dict[str, RainStats | None] = {}
        if len(areas) > 100:
            bounds = np.asarray([area.geometry.bounds for area in areas])
            envelope = Area('WINDOW','Window','Lima','','LIMA_OBSERVATION',0,0,
                box(bounds[:,0].min(),bounds[:,1].min(),bounds[:,2].max(),bounds[:,3].max()))
            rows, cols = _window(ds,transformer,h,envelope)
            rain = ds['RRQPE'][rows,cols].values.astype('float64')
            dqf = ds['DQF'][rows,cols].values
            xx, yy = np.meshgrid(ds['x'].values[cols]*h,ds['y'].values[rows]*h)
            lon, lat = inverse.transform(xx,yy)
            per_area = batch_rain_stats(rain,dqf,np.asarray(lon),np.asarray(lat),areas)
            try:
                import rain_overlay
                overlay = rain_overlay.from_dataset(ds,transformer,h,key)
            except Exception as exc:
                print(f'::warning::Capa de lluvia no disponible: {exc!r}')
                overlay = None
            return RainResult(scan_time=_parse_iso(ds.attrs['time_coverage_end']),s3_key=key,per_area=per_area,overlay=overlay)
        for area in areas:
            rows, cols = _window(ds, transformer, h, area)
            rain = ds["RRQPE"][rows, cols].values.astype("float64")
            dqf = ds["DQF"][rows, cols].values
            xx, yy = np.meshgrid(ds["x"].values[cols] * h, ds["y"].values[rows] * h)
            lon, lat = inverse.transform(xx, yy)
            per_area[area.id] = area_rain_stats(rain, dqf, np.asarray(lon), np.asarray(lat), area)
        scan_time = _parse_iso(ds.attrs["time_coverage_end"])
    finally:
        ds.close()
    return RainResult(scan_time=scan_time, s3_key=key, per_area=per_area)


def batch_rain_stats(rain, dqf, lon, lat, areas):
    """One regional raster window; exact polygon-center tests via spatial index.

    Matches area_rain_stats, including its nearest-center fallback for subpixel areas.
    Missing or degraded pixels never become zero rainfall.
    """
    valid_coordinates = np.isfinite(lon.ravel()) & np.isfinite(lat.ravel())
    values = rain.ravel()[valid_coordinates]
    quality = dqf.ravel()[valid_coordinates]
    if not valid_coordinates.any():
        return {area.id:None for area in areas}
    tree = STRtree(shapely.points(lon.ravel()[valid_coordinates],lat.ravel()[valid_coordinates]))
    results = {}
    for area in areas:
        indices = tree.query(area.geometry,predicate='contains')
        if not len(indices):
            indices = np.asarray([tree.nearest(Point(area.centroid_lon,area.centroid_lat))])
        selected = values[indices]
        good = (quality[indices]==0) & np.isfinite(selected) & (selected>=0)
        if good.sum()/len(indices) < config.MIN_GOOD_PIXEL_FRACTION:
            results[area.id] = None
        else:
            usable = selected[good]
            results[area.id] = RainStats(round(float(usable.max()),1),round(float(usable.mean()),1))
    return results


# ---------------------------------------------------------------------------
# Rayos (GLM-L2-LCFA)
# ---------------------------------------------------------------------------


def count_flashes(lats: np.ndarray, lons: np.ndarray, areas: list[Area]) -> dict[str, int]:
    if len(areas) <= 100:
        return {a.id: int(shapely.contains_xy(a.geometry, lons, lats).sum()) for a in areas}
    finite = np.isfinite(lons) & np.isfinite(lats)
    tree = STRtree([area.geometry for area in areas])
    # One flash may legitimately be inside several overlapping observation corridors.
    pairs = tree.query(shapely.points(lons[finite],lats[finite]),predicate='within')
    counts = np.bincount(pairs[1],minlength=len(areas))
    return {area.id:int(counts[index]) for index,area in enumerate(areas)}


def read_glm(fs, areas: list[Area], now: datetime, bucket: str = config.GOES_BUCKET) -> dict[str, int | None]:
    """Cuenta los rayos de los últimos 15 min por quebrada.

    Busca también en la hora anterior (el código original perdía la ventana al cambiar de hora).
    Si faltan archivos en la ventana, publica `None`: un conteo parcial podría parecer calma.
    """
    unknown: dict[str, int | None] = {a.id: None for a in areas}
    window_start = now - timedelta(minutes=config.GLM_WINDOW_MINUTES)
    keys = [
        k
        for k in list_recent_keys(fs, config.GLM_PRODUCT, now, 1, bucket)
        if window_start <= start_time_from_key(k) <= now
    ]

    expected = config.GLM_WINDOW_MINUTES * 3  # un archivo cada 20 s
    if len(keys) < expected * config.GLM_MIN_FILE_FRACTION:
        return unknown

    def load(key: str) -> tuple[np.ndarray, np.ndarray]:
        with xr.open_dataset(io.BytesIO(fs.cat(key)), engine="h5netcdf") as ds:
            return ds["flash_lat"].values.astype("float64"), ds["flash_lon"].values.astype("float64")

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            parts = list(pool.map(load, keys))
    except Exception:
        return unknown

    lats = np.concatenate([p[0] for p in parts]) if parts else np.empty(0)
    lons = np.concatenate([p[1] for p in parts]) if parts else np.empty(0)
    return count_flashes(lats, lons, areas)
