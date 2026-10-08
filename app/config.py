from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path


def parse_domains(value: str) -> tuple[str, ...]:
    domains: list[str] = []
    for raw in str(value or "").split(","):
        domain = raw.strip().lower()
        if domain and domain not in domains:
            domains.append(domain)
    return tuple(domains)


@dataclass(frozen=True)
class ServiceConfig:
    domains: tuple[str, ...]
    api_token: str
    db_path: Path
    smtp_host: str = "0.0.0.0"
    smtp_port: int = 25
    http_host: str = "0.0.0.0"
    http_port: int = 8081

    @property
    def default_domain(self) -> str:
        return self.domains[0] if self.domains else "example.com"

    def allows_domain(self, domain: str) -> bool:
        return domain.strip().lower() in self.domains


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SMTP 接码服务（SMTP + HTTP）")
    parser.add_argument("--domain", default=os.environ.get("DOMAIN", ""), help="接收域名，多个用英文逗号分隔")
    parser.add_argument("--api-token", default=os.environ.get("API_TOKEN", ""), help="HTTP API token（为空时读取环境变量 API_TOKEN）")
    parser.add_argument("--db-path", default=os.environ.get("DB_PATH", "/var/lib/smtp-code-service/messages.db"), help="SQLite 路径")
    parser.add_argument("--smtp-host", default=os.environ.get("SMTP_HOST", "0.0.0.0"), help="SMTP 监听地址")
    parser.add_argument("--smtp-port", type=int, default=int(os.environ.get("SMTP_PORT", 25)), help="SMTP 监听端口")
    parser.add_argument("--http-host", default=os.environ.get("HTTP_HOST", "0.0.0.0"), help="HTTP 监听地址")
    parser.add_argument("--http-port", type=int, default=int(os.environ.get("HTTP_PORT", 8081)), help="HTTP 监听端口")
    return parser.parse_args(args)


def load_config(args: argparse.Namespace | None = None) -> ServiceConfig:
    if args is None:
        args = parse_args()

    domain_str = str(args.domain or os.environ.get("DOMAIN", "")).strip()
    api_token = str(args.api_token or os.environ.get("API_TOKEN", "")).strip()

    domains = parse_domains(domain_str)
    if not domains:
        raise ValueError("缺少配置: domain 不能为空 (通过 --domain 或 DOMAIN 环境变量指定)")
    if not api_token:
        raise ValueError("缺少配置: api-token 不能为空 (通过 --api-token 或 API_TOKEN 环境变量指定)")

    return ServiceConfig(
        domains=domains,
        api_token=api_token,
        db_path=Path(args.db_path),
        smtp_host=str(args.smtp_host),
        smtp_port=int(args.smtp_port),
        http_host=str(args.http_host),
        http_port=int(args.http_port),
    )
