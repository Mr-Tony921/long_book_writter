import getpass
import json
import uuid

import requests

from longbookwritter.config import Settings
from longbookwritter.llm.base import BaseTextClient, LLMResult


class DoubaoTextClient(BaseTextClient):
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.doubao_use_ip_route and settings.doubao_api_ip:
            self.api_url = f"http://{settings.doubao_api_ip}/gateway/chatTask/callResult"
        else:
            self.api_url = f"{settings.doubao_api_base_url}/gateway/chatTask/callResult"

        self.headers = {
            "api-key": settings.doubao_api_key,
            "Content-Type": "application/json",
            "Host": settings.doubao_api_host,
        }
        self.proxies = None
        if settings.doubao_proxy_url:
            self.proxies = {"http": settings.doubao_proxy_url, "https": settings.doubao_proxy_url}

    def generate_text(self, prompt_text: str, model: str | None = None) -> LLMResult:
        model_name = model or self.settings.doubao_lite_model
        request_data = self._build_request_data(prompt_text=prompt_text, model_name=model_name)

        if self.settings.doubao_enable_stream and self.settings.doubao_stream_first:
            stream_res = self._request_streaming(request_data=request_data)
            if stream_res.success:
                return stream_res
            non_stream_res = self._request_non_stream(request_data=request_data)
            if non_stream_res.success:
                return non_stream_res
            return LLMResult(
                success=False,
                content=(
                    f"stream_failed=({stream_res.content}); "
                    f"fallback_non_stream_failed=({non_stream_res.content})"
                ),
                error_type=non_stream_res.error_type or stream_res.error_type,
                status_code=non_stream_res.status_code or stream_res.status_code,
                raw_response=non_stream_res.raw_response or stream_res.raw_response,
            )

        return self._request_non_stream(request_data=request_data)

    def _build_request_data(self, prompt_text: str, model_name: str) -> dict:
        return {
            "server_name": "longbookwritter",
            "model": model_name,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt_text}]}],
            "transaction_id": f"{getpass.getuser()}-{model_name}-{uuid.uuid4().hex[:8]}",
            "channel_code": self.settings.doubao_channel_code,
        }

    def _request_non_stream(self, request_data: dict) -> LLMResult:
        try:
            session = requests.Session()
            session.trust_env = False
            response = session.post(
                self.api_url,
                headers=self.headers,
                data=json.dumps(request_data, ensure_ascii=False).encode("utf-8"),
                timeout=self.settings.request_timeout_seconds,
                proxies=self.proxies,
            )
        except requests.exceptions.RequestException as exc:
            return LLMResult(success=False, content=f"request failed: {exc}", error_type="network_error")

        return self._parse_sync_response(response)

    def _request_streaming(self, request_data: dict) -> LLMResult:
        stream_payload = dict(request_data)
        stream_payload["stream"] = True
        try:
            session = requests.Session()
            session.trust_env = False
            response = session.post(
                self.api_url,
                headers=self.headers,
                data=json.dumps(stream_payload, ensure_ascii=False).encode("utf-8"),
                timeout=(10, self.settings.request_timeout_seconds),
                proxies=self.proxies,
                stream=True,
            )
        except requests.exceptions.RequestException as exc:
            return LLMResult(success=False, content=f"stream request failed: {exc}", error_type="network_error")

        if response.status_code != 200:
            return LLMResult(
                success=False,
                content=f"stream http {response.status_code}: {response.text}",
                error_type="http_error",
                status_code=response.status_code,
                raw_response=response.text,
            )

        content_type = (response.headers.get("Content-Type") or "").lower()
        if "text/event-stream" in content_type:
            chunks: list[str] = []
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line in {"[DONE]", "done"}:
                    break
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                piece = self._extract_text_piece(obj)
                if piece:
                    chunks.append(piece)
            full_text = "".join(chunks).strip()
            if full_text:
                return LLMResult(success=True, content=full_text)
            return LLMResult(
                success=False,
                content="stream parse failed: empty chunks",
                error_type="parse_error",
            )

        # Some gateways may ignore stream=true and still return normal JSON.
        try:
            response_json = response.json()
        except Exception as exc:  # noqa: BLE001
            return LLMResult(
                success=False,
                content=f"stream fallback invalid json: {exc}",
                error_type="parse_error",
                raw_response=response.text,
            )
        return self._parse_response_json(response_json=response_json, raw_text=response.text)

    def _parse_sync_response(self, response: requests.Response) -> LLMResult:
        if response.status_code != 200:
            return LLMResult(
                success=False,
                content=f"http {response.status_code}: {response.text}",
                error_type="http_error",
                status_code=response.status_code,
                raw_response=response.text,
            )
        try:
            response_json = response.json()
        except Exception as exc:  # noqa: BLE001
            return LLMResult(
                success=False,
                content=f"invalid json response: {exc}",
                error_type="parse_error",
                raw_response=response.text,
            )
        return self._parse_response_json(response_json=response_json, raw_text=response.text)

    def _parse_response_json(self, response_json: dict, raw_text: str) -> LLMResult:
        if response_json.get("code") != 10000:
            return LLMResult(
                success=False,
                content=f"api business error: {response_json}",
                error_type="business_error",
                raw_response=json.dumps(response_json, ensure_ascii=False),
            )

        try:
            content = response_json["data"]["response_content"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            return LLMResult(
                success=False,
                content=f"response parse failed: {exc}",
                error_type="parse_error",
                raw_response=raw_text,
            )
        return LLMResult(success=True, content=content)

    def _extract_text_piece(self, obj: dict) -> str:
        paths = [
            ("data", "response_content", "choices", 0, "delta", "content"),
            ("data", "response_content", "choices", 0, "message", "content"),
            ("choices", 0, "delta", "content"),
            ("choices", 0, "message", "content"),
        ]
        for path in paths:
            cur = obj
            ok = True
            for key in path:
                if isinstance(key, int):
                    if not isinstance(cur, list) or len(cur) <= key:
                        ok = False
                        break
                    cur = cur[key]
                else:
                    if not isinstance(cur, dict) or key not in cur:
                        ok = False
                        break
                    cur = cur[key]
            if ok and isinstance(cur, str):
                return cur
        return ""

