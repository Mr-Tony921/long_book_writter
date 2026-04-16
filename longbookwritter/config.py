import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    projects_dir: Path
    request_timeout_seconds: int
    doubao_api_key: str
    doubao_model: str
    doubao_lite_model: str
    doubao_channel_code: str
    doubao_api_host: str
    doubao_api_base_url: str
    doubao_api_ip: str
    doubao_use_ip_route: bool
    doubao_proxy_url: str | None
    doubao_enable_stream: bool
    doubao_stream_first: bool
    default_naming_count: int


def load_settings() -> Settings:
    root = Path(__file__).resolve().parents[1]
    host = os.getenv("DOUBAO_API_HOST", "api.schedule.mtc.sensetime.com")
    return Settings(
        root_dir=root,
        projects_dir=root / "projects",
        request_timeout_seconds=int(os.getenv("LONGBOOKWRITTER_REQUEST_TIMEOUT", "180")),
        doubao_api_key=os.getenv("DOUBAO_API_KEY", "89e5c2204ffaca8086cad6dee45ef43f"),
        doubao_model=os.getenv("DOUBAO_MODEL", "doubao-seed-2.0-260215"),
        doubao_lite_model=os.getenv("DOUBAO_LITE_MODEL", "doubao-seed-2.0-lite-260215"),
        doubao_channel_code=os.getenv("DOUBAO_CHANNEL_CODE", "doubao"),
        doubao_api_host=host,
        doubao_api_base_url=os.getenv("DOUBAO_API_BASE_URL", f"http://{host}"),
        doubao_api_ip=os.getenv("DOUBAO_API_IP", "172.19.57.3"),
        doubao_use_ip_route=os.getenv("DOUBAO_USE_IP_ROUTE", "true").lower() in {"1", "true", "yes"},
        doubao_proxy_url=os.getenv("DOUBAO_PROXY_URL") or None,
        doubao_enable_stream=os.getenv("DOUBAO_ENABLE_STREAM", "false").lower() in {"1", "true", "yes"},
        doubao_stream_first=os.getenv("DOUBAO_STREAM_FIRST", "false").lower() in {"1", "true", "yes"},
        default_naming_count=int(os.getenv("LONGBOOKWRITTER_DEFAULT_NAMING_COUNT", "8")),
    )
