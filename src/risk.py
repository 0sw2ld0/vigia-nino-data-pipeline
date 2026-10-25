"""Semáforo por quebrada a partir de lo que observa el satélite.

Reglas de diseño:
- Sin lectura de lluvia del satélite -> UNKNOWN (gris). La falta de datos nunca es calma.
- Los rayos y el acumulado solo pueden SUBIR el nivel; si faltan, se ignoran.
- Los textos describen la observación; no predicen huaicos ni declaran zonas seguras.
- Los umbrales vienen del perfil regional de cada quebrada (`config.THRESHOLD_PROFILES`);
  son provisionales y no están calibrados.
"""

from dataclasses import dataclass

TEXTS = {
    "RED": (
        "El satélite detecta lluvia intensa en la parte alta",
        "Aléjate del cauce de la quebrada, ten lista tu mochila de emergencia y sigue las indicaciones de INDECI y SENAMHI.",
    ),
    "YELLOW": (
        "El satélite detecta lluvia en la parte alta",
        "Mantente atento a los avisos de SENAMHI y aléjate del cauce si la lluvia aumenta.",
    ),
    "GREEN": (
        "Sin lluvias importantes detectadas por el satélite",
        "El satélite no ve lluvia fuerte en la parte alta en este momento. Revisa igual los avisos oficiales.",
    ),
    "UNKNOWN": (
        "Sin datos confiables ahora",
        "No pudimos leer el satélite para esta zona. Revisa los avisos oficiales de SENAMHI.",
    ),
}


@dataclass(frozen=True)
class Status:
    level: str
    headline: str
    advice: str

    def as_dict(self) -> dict:
        return {"level": self.level, "headline": self.headline, "advice": self.advice}


def classify(
    rain_max_mmh: float | None,
    lightning_15m: int | None,
    rain_accum_72h_mm: float | None,
    thresholds: dict,
) -> Status:
    t = thresholds
    if rain_max_mmh is None:
        level = "UNKNOWN"
    elif rain_max_mmh >= t["red_rain_mmh"] or (
        lightning_15m is not None
        and lightning_15m >= t["red_lightning"]
        and rain_max_mmh >= t["red_rain_with_lightning_mmh"]
    ):
        level = "RED"
    elif (
        rain_max_mmh >= t["yellow_rain_mmh"]
        or (lightning_15m is not None and lightning_15m >= t["yellow_lightning"])
        or (
            rain_accum_72h_mm is not None
            and rain_accum_72h_mm >= t["yellow_wet_accum_72h_mm"]
            and rain_max_mmh >= t["yellow_wet_rain_mmh"]
        )
    ):
        level = "YELLOW"
    else:
        level = "GREEN"

    headline, advice = TEXTS[level]
    return Status(level=level, headline=headline, advice=advice)
