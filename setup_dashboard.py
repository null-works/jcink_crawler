#!/usr/bin/env python3
"""Set up dashboard password for The Watcher web interface."""
import base64
import secrets
import sys
import shutil
from pathlib import Path


ENV_FILE = Path(".env")
ENV_EXAMPLE = Path(".env.example")


def main():
    print()
    print("  The Watcher - Dashboard Password Setup")
    print("  " + "=" * 40)
    print()

    # Ensure .env exists
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            shutil.copy(ENV_EXAMPLE, ENV_FILE)
            print("  Created .env from .env.example")
            print()
        else:
            print("  ERROR: Neither .env nor .env.example found.")
            print("  Run this from the project root.")
            sys.exit(1)

    password = input("  Enter a dashboard password: ").strip()
    if not password:
        print("  No password entered. Aborting.")
        sys.exit(1)

    confirm = input("  Confirm password: ").strip()
    if password != confirm:
        print("  Passwords don't match. Aborting.")
        sys.exit(1)

    # Base64-encode the password to avoid all shell/docker escaping issues
    password_b64 = base64.b64encode(password.encode("utf-8")).decode("utf-8")
    secret_key = secrets.token_urlsafe(32)

    # Read existing .env
    content = ENV_FILE.read_text()

    # Update or append DASHBOARD_PASSWORD_B64
    if "DASHBOARD_PASSWORD_B64=" in content:
        lines = content.split("\n")
        lines = [
            f"DASHBOARD_PASSWORD_B64={password_b64}" if l.startswith("DASHBOARD_PASSWORD_B64=") else l
            for l in lines
        ]
        content = "\n".join(lines)
    else:
        content = content.rstrip("\n") + f"\nDASHBOARD_PASSWORD_B64={password_b64}\n"

    # Update or append DASHBOARD_SECRET_KEY (only if it's the default)
    if "DASHBOARD_SECRET_KEY=change-me-in-production" in content:
        content = content.replace(
            "DASHBOARD_SECRET_KEY=change-me-in-production",
            f"DASHBOARD_SECRET_KEY={secret_key}",
        )
    elif "DASHBOARD_SECRET_KEY=" not in content:
        content = content.rstrip("\n") + f"\nDASHBOARD_SECRET_KEY={secret_key}\n"

    ENV_FILE.write_text(content)

    print()
    print("  Dashboard password configured!")
    print()
    print(f"  Login with:  {password}")
    print(f"  Stored as:   DASHBOARD_PASSWORD_B64={password_b64}")
    print()
    print("  Saved to .env. Restart to apply:")
    print("    ./deploy.sh")
    print("  Or manually:")
    print("    docker compose up --build -d")
    print()
    print("  Dashboard: http://localhost:8943/dashboard")
    print()


if __name__ == "__main__":
    main()
