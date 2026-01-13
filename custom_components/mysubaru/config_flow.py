"""Config flow for MySubaru websocket integration with 2FA assist."""

from __future__ import annotations

import re
from typing import Any, Dict

import aiohttp
import async_timeout
import secrets
import voluptuous as vol
from homeassistant import config_entries

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_REGION,
    CONF_USERNAME,
    CONF_WS_URL,
    DEFAULT_REGION,
    DEFAULT_WS_URL,
    DOMAIN,
)


def _http_base(ws_url: str) -> str:
    # naive conversion: ws://host:port/ws -> http://host:port
    if ws_url.startswith("wss://"):
        return re.sub(r"^wss://", "https://", ws_url).rsplit("/", 1)[0]
    return re.sub(r"^ws://", "http://", ws_url).rsplit("/", 1)[0]


class MySubaruConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._ws_url: str | None = None
        self._creds: Dict[str, Any] | None = None

    async def _get_json(self, url: str) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            with async_timeout.timeout(10):
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    return await resp.json()

    async def _post_json(
        self, url: str, payload: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            with async_timeout.timeout(10):
                async with session.post(url, json=payload) as resp:
                    resp.raise_for_status()
                    try:
                        return await resp.json()
                    except Exception:
                        return {}

    async def async_step_user(self, user_input=None):
        errors: Dict[str, str] = {}

        if user_input is not None:
            ws_url: str = user_input[CONF_WS_URL]
            self._ws_url = ws_url
            self._creds = {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_PIN: user_input[CONF_PIN],
                CONF_DEVICE_ID: user_input[CONF_DEVICE_ID],
                CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                CONF_REGION: user_input.get(CONF_REGION, DEFAULT_REGION),
            }

            base = _http_base(ws_url)
            config_url = f"{base}/auth/config"
            try:
                await self._post_json(config_url, self._creds)
                status = await self._get_json(f"{base}/auth/status")
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                if status.get("requires_2fa"):
                    try:
                        await self._post_json(f"{base}/auth/send_code")
                    except Exception:
                        errors["base"] = "cannot_connect"
                    if not errors:
                        return await self.async_step_verify()
                else:
                    data = dict(user_input)
                    return self.async_create_entry(
                        title="MySubaru Websocket", data=data
                    )

        default_device_id = (
            user_input.get(CONF_DEVICE_ID) if user_input else secrets.token_hex(16)
        )
        default_device_name = (
            user_input.get(CONF_DEVICE_NAME)
            if user_input
            else "Hassio MySubaru Websocket Add-on"
        )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_WS_URL, default=DEFAULT_WS_URL): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_PIN): str,
                vol.Required(CONF_DEVICE_ID, default=default_device_id): str,
                vol.Required(CONF_DEVICE_NAME, default=default_device_name): str,
                vol.Optional(CONF_REGION, default=DEFAULT_REGION): vol.In(
                    ["USA", "CAN"]
                ),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_verify(self, user_input=None):
        errors: Dict[str, str] = {}
        if user_input is not None and self._ws_url and self._creds:
            code = user_input["code"]
            base = _http_base(self._ws_url)
            verify_url = f"{base}/auth/verify?code={code}"
            try:
                await self._post_json(verify_url)
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                data = {CONF_WS_URL: self._ws_url, **self._creds}
                return self.async_create_entry(title="MySubaru Websocket", data=data)

        data_schema = vol.Schema({vol.Required("code"): str})
        return self.async_show_form(
            step_id="verify", data_schema=data_schema, errors=errors
        )
