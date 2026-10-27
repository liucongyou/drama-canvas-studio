# -*- coding: utf-8 -*-
"""Local tenant administration CLI. Never prints provider or TOS secrets."""
from __future__ import annotations

import argparse
import os
import sys

import tenant_bootstrap
import tenant_registry


def _base_domain(args: argparse.Namespace) -> str:
    value = str(args.base_domain or os.environ.get("CANVAS_BASE_DOMAIN", "")).strip()
    if not value:
        raise SystemExit("缺少基础域名：请设置 CANVAS_BASE_DOMAIN 或传 --base-domain")
    return value.lower().rstrip(".")


def _tenant(slug: str) -> dict:
    tenant = tenant_registry.get_tenant_by_slug(slug)
    if not tenant:
        raise SystemExit(f"租户不存在：{slug}")
    return tenant


def _invite_url(base_domain: str, slug: str, token: str) -> str:
    return f"https://{slug}.{base_domain}/invite/{token}"


def cmd_create(args: argparse.Namespace) -> int:
    try:
        tenant = tenant_bootstrap.create_tenant(args.slug, name=args.name or "")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"已创建租户：{tenant['slug']} ({tenant['id']})")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = tenant_registry.list_tenants(include_disabled=True)
    if not rows:
        print("暂无租户")
        return 0
    print("SLUG\tSTATUS\tNAME\tID")
    for tenant in rows:
        print(f"{tenant['slug']}\t{tenant['status']}\t{tenant['name']}\t{tenant['id']}")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    tenant = tenant_registry.set_tenant_status(args.slug, "disabled")
    print(f"已停用：{tenant['slug']}")
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    tenant = tenant_registry.set_tenant_status(args.slug, "active")
    print(f"已启用：{tenant['slug']}")
    return 0


def cmd_invite(args: argparse.Namespace) -> int:
    tenant = _tenant(args.slug)
    invite = tenant_registry.create_invite(
        tenant["id"],
        ttl_seconds=int(args.ttl_hours * 3600),
    )
    print(f"邀请ID：{invite['id']}")
    print(f"有效期：{invite['expires_at']}")
    print(_invite_url(_base_domain(args), tenant["slug"], invite["token"]))
    return 0


def cmd_rotate_invite(args: argparse.Namespace) -> int:
    tenant = _tenant(args.slug)
    invite = tenant_registry.rotate_invite(
        tenant["id"],
        ttl_seconds=int(args.ttl_hours * 3600),
    )
    print(f"旧邀请已撤销，新邀请ID：{invite['id']}")
    print(f"有效期：{invite['expires_at']}")
    print(_invite_url(_base_domain(args), tenant["slug"], invite["token"]))
    return 0


def cmd_revoke_invite(args: argparse.Namespace) -> int:
    if tenant_registry.revoke_invite(args.invite_id):
        print(f"已撤销邀请：{args.invite_id}")
        return 0
    print("邀请不存在或已经失效")
    return 1


def cmd_cleanup(args: argparse.Namespace) -> int:
    result = tenant_registry.purge_expired(older_than_seconds=int(args.older_hours * 3600))
    print(f"已清理 invites={result['invites']} sessions={result['sessions']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="戏剧画布租户管理")
    parser.add_argument("--base-domain", default="", help="例如 example.com")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="创建租户")
    create.add_argument("slug")
    create.add_argument("--name", default="")
    create.set_defaults(func=cmd_create)

    listing = sub.add_parser("list", help="列出租户")
    listing.set_defaults(func=cmd_list)

    disable = sub.add_parser("disable", help="停用租户")
    disable.add_argument("slug")
    disable.set_defaults(func=cmd_disable)

    enable = sub.add_parser("enable", help="启用租户")
    enable.add_argument("slug")
    enable.set_defaults(func=cmd_enable)

    invite = sub.add_parser("invite", help="生成一次性邀请")
    invite.add_argument("slug")
    invite.add_argument("--ttl-hours", type=float, default=24)
    invite.set_defaults(func=cmd_invite)

    rotate = sub.add_parser("rotate-invite", help="撤销旧邀请并生成新邀请")
    rotate.add_argument("slug")
    rotate.add_argument("--ttl-hours", type=float, default=24)
    rotate.set_defaults(func=cmd_rotate_invite)

    revoke = sub.add_parser("revoke-invite", help="按邀请 ID 撤销")
    revoke.add_argument("invite_id")
    revoke.set_defaults(func=cmd_revoke_invite)

    cleanup = sub.add_parser("cleanup", help="清理过期邀请和会话")
    cleanup.add_argument("--older-hours", type=float, default=24 * 7)
    cleanup.set_defaults(func=cmd_cleanup)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        raise SystemExit(130)

