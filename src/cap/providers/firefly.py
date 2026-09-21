"""Adobe Firefly Services provider (Firefly API v3, async jobs).

Implemented against the public Firefly Services documentation. Firefly API credentials are only
issued to enterprise organizations (created by an org admin in Adobe Developer Console), so this
adapter is exercised by unit tests with a mocked transport rather than live calls. At a customer
with Firefly Services, switching is `--provider firefly` plus two env vars.

Why Firefly in production: commercially safe training data, IP indemnification, Custom Models
trained on the brand, and Generative Expand as a first-class reframing operation.
"""

from __future__ import annotations

import os
import time

import httpx

from .base import ImageProvider, ProviderError, ProviderStatus, open_image, to_png, with_retries

IMS = "https://ims-na1.adobelogin.com/ims/token/v3"
API = "https://firefly-api.adobe.io"
SCOPES = "openid,AdobeID,session,additional_info,read_organizations,firefly_api,ff_apis"

# Output sizes Firefly accepts vary by model version; map target aspect ratios to supported sizes.
SIZES = {1.0: (2048, 2048), 16 / 9: (2688, 1536), 9 / 16: (1536, 2688), 4 / 5: (1792, 2304)}


def nearest_size(ratio: float) -> tuple[int, int]:
    return SIZES[min(SIZES, key=lambda r: abs(r - ratio))]


class FireflyProvider(ImageProvider):
    name = "firefly"
    supports_expand = True
    hero_size = (2048, 2048)
    requests_per_minute = float(os.getenv("FIREFLY_RPM", "4"))  # documented default org limit

    def __init__(self, client: httpx.Client | None = None, poll_interval: float = 2.0):
        self.client_id = os.getenv("FIREFLY_CLIENT_ID", "")
        self.client_secret = os.getenv("FIREFLY_CLIENT_SECRET", "")
        self.model = os.getenv("FIREFLY_MODEL_VERSION", "")  # optional x-model-version header
        self.est_cost_per_image = float(os.getenv("FIREFLY_EST_COST_PER_IMAGE", "0.04"))
        self.http = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))
        self.poll_interval = poll_interval
        self._token: tuple[str, float] | None = None
        super().__init__()
        self.model = self.model or "firefly-image (default)"

    def status(self) -> ProviderStatus:
        if not (self.client_id and self.client_secret):
            return ProviderStatus(False, "set FIREFLY_CLIENT_ID and FIREFLY_CLIENT_SECRET (enterprise org credential)")
        return ProviderStatus(True, "Firefly Services v3")

    # --- auth -----------------------------------------------------------------------------
    def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        if not (self.client_id and self.client_secret):
            raise ProviderError("Firefly credentials are not configured")

        def call():
            r = self.http.post(
                IMS,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": SCOPES,
                },
            )
            r.raise_for_status()
            return r

        j = with_retries(call).json()
        self._token = (j["access_token"], time.time() + float(j.get("expires_in", 3600)))
        return self._token[0]

    def _headers(self, json_body: bool = True) -> dict:
        h = {"x-api-key": self.client_id, "Authorization": f"Bearer {self._access_token()}"}
        if json_body:
            h["Content-Type"] = "application/json"
        if os.getenv("FIREFLY_MODEL_VERSION"):
            h["x-model-version"] = os.environ["FIREFLY_MODEL_VERSION"]
        return h

    # --- async job helpers ------------------------------------------------------------------
    def _submit_and_wait(self, path: str, body: dict, timeout: float = 300.0) -> bytes:
        def submit():
            r = self.http.post(f"{API}{path}", json=body, headers=self._headers())
            r.raise_for_status()
            return r

        job = with_retries(submit).json()
        status_url = job.get("statusUrl")
        if not status_url:
            raise ProviderError(f"no statusUrl in response: {job}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = with_retries(lambda: _raise(self.http.get(status_url, headers=self._headers(False))))
            j = r.json()
            state = j.get("status")
            if state == "succeeded":
                url = j["result"]["outputs"][0]["image"]["url"]
                img = with_retries(lambda u=url: _raise(self.http.get(u)))
                return to_png(open_image(img.content))
            if state in ("failed", "cancelled"):
                raise ProviderError(f"Firefly job {state}: {j}")
            time.sleep(self.poll_interval)
        raise ProviderError(f"Firefly job timed out after {timeout}s")

    # --- operations -------------------------------------------------------------------------
    def generate(self, prompt: str, size: tuple[int, int]) -> bytes:
        self._tick()
        w, h = nearest_size(size[0] / size[1])
        body = {
            "prompt": prompt,
            "numVariations": 1,
            "contentClass": "photo",
            "size": {"width": w, "height": h},
            "negativePrompt": "text, letters, words, logos, watermark",
        }
        return self._submit_and_wait("/v3/images/generate-async", body)

    def expand(self, image: bytes, canvas: tuple[int, int], prompt: str) -> bytes:
        self._tick()

        def upload():
            r = self.http.post(
                f"{API}/v2/storage/image",
                content=image,
                headers={**self._headers(False), "Content-Type": "image/png"},
            )
            r.raise_for_status()
            return r

        upload_id = with_retries(upload).json()["images"][0]["id"]
        w, h = nearest_size(canvas[0] / canvas[1])
        body = {
            "numVariations": 1,
            "size": {"width": w, "height": h},
            "image": {"source": {"uploadId": upload_id}},
            "placement": {"alignment": {"horizontal": "center", "vertical": "center"}},
            "prompt": prompt,
        }
        return self._submit_and_wait("/v3/images/expand-async", body)


def _raise(r: httpx.Response) -> httpx.Response:
    r.raise_for_status()
    return r
