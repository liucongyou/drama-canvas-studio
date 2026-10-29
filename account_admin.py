#!/usr/bin/env python3
"""Small local admin tool for emergency account recovery."""

import argparse
import json
import secrets
import string

import tenant_registry


def generated_password(length=14):
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(char.islower() for char in value)
            and any(char.isupper() for char in value)
            and any(char.isdigit() for char in value)
            and any(char in "!@#$%" for char in value)
        ):
            return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--password")
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()

    account = tenant_registry.get_account_by_username(args.username)
    if not account:
        raise SystemExit(f"账号不存在：{args.username}")
    password = args.password or generated_password()
    if args.enable and account.get("status") != "active":
        tenant_registry.set_account_status(account["id"], "active")
    tenant_registry.set_account_password(account["id"], password)
    updated = tenant_registry.get_account_by_id(account["id"])
    print(json.dumps({
        "username": updated["username"],
        "status": updated["status"],
        "temporaryPassword": password,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
