#!/usr/bin/env python3
"""Cliente asíncrono para LibreLinkUP API.

Basado en dodog/librelink (Home Assistant integration).
Maneja:
  - Login con email/password → Bearer token
  - Listado de pacientes vinculados con medición actual
  - (Sin endpoint de historial dedicado — solo medición actual por paciente)

Soporta regiones: global, us, latam.
Se configura con librelinkup_config.json o variables de entorno.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

import aiohttp

from .models import Measurement, Patient, SensorInfo, TrendArrow

logger = logging.getLogger(__name__)

# Rutas y credenciales
CONFIG_PATH = Path(__file__).parent.parent / "librelinkup_config.json"

# Headers requeridos por la API (dodog/librelink const.py)
PRODUCT_HEADER = "llu.android"
VERSION_HEADER = "4.17.0"

# Endpoints por región — basados en dodog/librelink (Home Assistant integration)
# Todos usan el prefijo /llu/ (LibreLink Up), NO /bbs/ ni /lsl/
REGIONS = {
    "global": {
        "base": "https://api.libreview.io",
        "login": "https://api.libreview.io/llu/auth/login",
        "patients": "https://api.libreview.io/llu/connections",
    },
    "us": {
        "base": "https://api-us.libreview.io",
        "login": "https://api-us.libreview.io/llu/auth/login",
        "patients": "https://api-us.libreview.io/llu/connections",
    },
    "latam": {
        "base": "https://api.libreview.io",
        "login": "https://api.libreview.io/llu/auth/login",
        "patients": "https://api.libreview.io/llu/connections",
    },
}


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Carga la configuración desde JSON o retorna defaults."""
    cfg_path = path or CONFIG_PATH
    if cfg_path.exists():
        with open(cfg_path) as f:
            return json.load(f)
    return {
        "email": "",
        "password": "",
        "region": "latam",
    }


def _extract_jwt_account_id(token: str) -> Optional[str]:
    """Extrae el account ID del payload del JWT.

    El token JWT de LibreLinkUP contiene el account ID en el campo 'id'.
    """
    try:
        # JWT usa base64url encoding
        payload = token.split('.')[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        payload = payload.replace('-', '+').replace('_', '/')
        decoded = json.loads(base64.b64decode(payload))
        return decoded.get("id")
    except Exception:
        return None


class LibreLinkClient:
    """Cliente asíncrono para LibreLinkUP API."""

    def __init__(self, email: str = "", password: str = "", region: str = "latam"):
        self.email = email
        self.password = password
        self.region = region
        self._token: Optional[str] = None
        self._account_id: Optional[str] = None
        self._account_hash: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None

    # --- helpers ---

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _close_session(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._close_session()

    # --- auth ---

    async def login(self) -> bool:
        """Autentica con email/password. Retorna True si exitoso.

        Usa los headers product/version requeridos por la API LibreLinkUP.
        Token se obtiene de response["data"]["authTicket"]["token"].
        Account ID se extrae del JWT payload.

        Status codes de la API:
          0 = OK (login directo)
          2 = Auth error (credenciales inválidas)
          4 = Terms of Use need acceptance (token válido pero requiere TOU)
        """
        endpoints = REGIONS[self.region]
        url = endpoints["login"]

        payload = {
            "email": self.email,
            "password": self.password,
        }

        session = await self._get_session()
        try:
            async with session.post(
                url, json=payload,
                headers={
                    "product": PRODUCT_HEADER,
                    "version": VERSION_HEADER,
                },
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Login falló {resp.status}: {text[:200]}")
                    return False

                data = await resp.json()

                # dodog/librelink: response["status"] == 2 = auth error
                status = data.get("status")
                if status == 2:
                    logger.error("Login falló — credenciales inválidas (status=2)")
                    return False

                # Token viene de data.authTicket.token (dodog/librelink async_login)
                self._token = (
                    data.get("data", {})
                    .get("authTicket", {})
                    .get("token")
                )

                # Account ID viene del JWT payload (no hay data.user.id en la respuesta)
                if self._token:
                    self._account_id = _extract_jwt_account_id(self._token)

                # Pre-calculamos el hash SHA-256 necesario para requests autenticados
                if self._account_id:
                    self._account_hash = hashlib.sha256(self._account_id.encode()).hexdigest()

                # Status=4 = Terms of Use need acceptance, pero el token es válido
                if status == 4:
                    logger.info(
                        f"Login exitoso (con TOU pendiente) — región={self.region}, "
                        f"accountId={self._account_id}"
                    )
                    return True

                if not self._token:
                    logger.error("No se obtuvo token de la respuesta")
                    logger.debug(f"Respuesta completa: {json.dumps(data)[:500]}")
                    return False

                logger.info(f"Login exitoso — región={self.region}, accountId={self._account_id}")
                return True
        except Exception as exc:
            logger.error(f"Login excepción: {exc}")
            return False

    @property
    def is_authenticated(self) -> bool:
        return self._token is not None

    def _headers(self) -> dict[str, str]:
        """Headers para requests autenticados.

        Incluye Authorization Bearer + Account-Id (SHA-256 hash) como
        requiere dodog/librelink _call_api con authenticated=True.
        """
        headers = {
            "product": PRODUCT_HEADER,
            "version": VERSION_HEADER,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._account_hash:
            headers["Account-Id"] = self._account_hash
        return headers

    # --- patients ---

    async def get_patients(self) -> list[Patient]:
        """Retorna lista de pacientes vinculados con datos actuales."""
        if not self.is_authenticated:
            raise RuntimeError("No autenticado — llama login() primero")

        endpoints = REGIONS[self.region]
        url = endpoints["patients"]

        session = await self._get_session()
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status not in (200, 204):
                text = await resp.text()
                raise RuntimeError(f"get_patients {resp.status}: {text[:200]}")

            data = await resp.json() if resp.status == 200 else {}

            # dodog/librelink: response["status"] == 0 = OK
            if data.get("status") != 0:
                logger.warning(f"API status={data.get('status')} — {json.dumps(data)[:300]}")

            return self._parse_patients(data)

    @staticmethod
    def _parse_patients(data: dict) -> list[Patient]:
        """Parsa la respuesta de /llu/connections.

        dodog/librelink: response["data"] es lista de pacientes.
        """
        items = data.get("data", [])
        if not items:
            # Intentar con respuesta directa
            if "patientId" in data:
                items = [data]
            else:
                logger.warning(f"Sin pacientes en respuesta: {json.dumps(data)[:300]}")
                return []

        patients = []
        for item in items:
            patient = LibreLinkClient._parse_patient(item)
            if patient:
                patients.append(patient)
        return patients

    @staticmethod
    def _parse_patient(data: dict) -> Optional[Patient]:
        """Parsa un solo paciente desde la respuesta de la API.

        Estructura basada en dodog/librelink Patient.from_api_response_data:
          data["patientId"], data["firstName"], data["lastName"]
          data["glucoseMeasurement"]["ValueInMgPerDl"], ["FactoryTimestamp"], ["TrendArrow"]
          data["targetLow"], data["targetHigh"]
          data["sensor"]["pt"], data["sensor"]["sn"], data["sensor"]["a"], data["sensor"]["e"]
        """
        patient = Patient(
            id=data.get("patientId", ""),
            first_name=data.get("firstName", ""),
            last_name=data.get("lastName", ""),
            date_of_birth=data.get("dateOfBirth"),
        )

        # Target range
        low = data.get("targetLow")
        high = data.get("targetHigh")
        if low is not None and high is not None:
            try:
                patient.target_low = float(low)
                patient.target_high = float(high)
            except (ValueError, TypeError):
                pass

        # Sensor info
        sensor_data = data.get("sensor", {})
        if sensor_data:
            patient.sensor = LibreLinkClient._parse_sensor(sensor_data)

        # Latest measurement
        meas_data = data.get("glucoseMeasurement")
        if meas_data:
            patient.measurement = LibreLinkClient._parse_measurement(meas_data)

        return patient

    @staticmethod
    def _parse_sensor(data: dict) -> Optional[SensorInfo]:
        """Parsa información del sensor.

        dodog/librelink estructura:
          data["a"] = applicationTimestamp (epoch seconds)
          data["e"] = expirationTimestamp (epoch seconds)
          data["pt"] = productType
          data["sn"] = serialNumber
        """
        activation = data.get("a")
        expiration = data.get("e")

        if not activation or not expiration:
            return None

        from datetime import datetime as _dt

        try:
            act_dt = _dt.fromtimestamp(activation)
            exp_dt = _dt.fromtimestamp(expiration)
        except (ValueError, TypeError, OSError):
            return None

        return SensorInfo(
            serial_number=f"{data.get('pt', '')}{data.get('sn', '')}",
            product_type=data.get("pt", ""),
            activation_timestamp=act_dt,
            expiration_timestamp=exp_dt,
            sensor_lifespan_days=data.get("sensorLifespan", 14),
            is_plus_model=data.get("isPlusSensor", False),
        )

    @staticmethod
    def _parse_measurement(data: dict) -> Optional[Measurement]:
        """Parsa una medición desde la API.

        dodog/librelink estructura:
          data["ValueInMgPerDl"] = glucosa en mg/dL
          data["FactoryTimestamp"] = "MM/DD/YYYY HH:MM:SS AM/PM"
          data["TrendArrow"] = int 1-5 (dodog/librelink lo mapea como trend int)
        """
        value = data.get("ValueInMgPerDl")
        if value is None:
            return None

        # Parsear timestamp formato "10/16/2026 09:30:00 PM"
        ts_str = data.get("FactoryTimestamp", "")
        try:
            from datetime import datetime as _dt
            dt = _dt.strptime(ts_str, "%m/%d/%Y %I:%M:%S %p")
        except (ValueError, TypeError):
            logger.warning(f"No se pudo parsear timestamp: {ts_str}")
            return None

        # Trend arrow — dodog/librelink lo trata como int (1-5)
        # 1=Decreasing fast, 2=Decreasing, 3=Stable, 4=Increasing, 5=Increasing fast
        trend_arrow = None
        trend_raw = data.get("TrendArrow")
        if trend_raw is not None:
            try:
                trend_val = int(trend_raw)
                trend_arrow = TrendArrow(trend_val)
            except (ValueError, TypeError, KeyError):
                pass

        return Measurement(
            timestamp=dt,
            value_mg_dl=float(value),
            trend_arrow=trend_arrow,
        )

    # --- history (básico: devuelve medición actual repetida) ---

    async def get_history(
        self,
        patient_id: str,
        hours: int = 24,
    ) -> list[Measurement]:
        """Retorna historial de mediciones para un paciente (últimas N horas).

        LibreLinkUP API no tiene endpoint dedicado de historial.
        Retorna la medición actual replicada ~hours*12 veces con timestamps
        decrementados de 5 en 5 minutos (estimación para el trend calculator).

        Para historial real, se necesitaría polling continuo.
        """
        if not self.is_authenticated:
            raise RuntimeError("No autenticado — llama login() primero")

        patients = await self.get_patients()
        patient = next((p for p in patients if p.id == patient_id), None)
        if patient and patient.measurement:
            # Retornar la medición actual + estimación retro
            from datetime import datetime, timedelta

            current = patient.measurement
            history = []
            now = current.timestamp

            # Generar ~288 mediciones estimadas (24h / 5min)
            n_points = hours * 12
            for i in range(n_points - 1, -1, -1):
                ts = now - timedelta(minutes=5 * i)
                # Crear copia con timestamp retro (valor estimado = actual)
                estimated = Measurement(
                    timestamp=ts,
                    value_mg_dl=current.value_mg_dl,
                    trend_arrow=current.trend_arrow,
                )
                history.append(estimated)

            return history

        return []
