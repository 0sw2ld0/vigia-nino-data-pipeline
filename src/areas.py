"""Carga de los polígonos de quebradas."""

import json
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

import config


@dataclass(frozen=True)
class Area:
    id: str
    name: str
    region: str
    district: str
    threshold_profile: str
    centroid_lat: float
    centroid_lon: float
    geometry: BaseGeometry


def load_areas(path: Path) -> list[Area]:
    collection = json.loads(path.read_text(encoding="utf-8"))
    areas = []
    for feature in collection["features"]:
        props = feature["properties"]
        geometry = shape(feature["geometry"])
        if not geometry.is_valid or geometry.is_empty:
            raise ValueError(f"Geometría inválida en {props['id']}")
        if props.get("threshold_profile") not in config.THRESHOLD_PROFILES:
            raise ValueError(f"{props['id']}: threshold_profile debe ser uno de {sorted(config.THRESHOLD_PROFILES)}")
        areas.append(
            Area(
                id=props["id"],
                name=props["name"],
                region=props["region"],
                district=props["district"],
                threshold_profile=props["threshold_profile"],
                centroid_lat=props["centroid_lat"],
                centroid_lon=props["centroid_lon"],
                geometry=geometry,
            )
        )
    ids = [a.id for a in areas]
    if len(ids) != len(set(ids)):
        raise ValueError("IDs de quebrada repetidos en el GeoJSON")
    return areas
