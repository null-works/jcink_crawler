#!/usr/bin/env python3
"""Set up dashboard password for The Watcher web interface."""
import secrets
import sys
import re


COMPOSE_FILE = "docker-compose.yml"


def main():
    print()
    print("  The Watcher - Dashboard Password Setup")
    print("  " + "=" * 40)
    print()

    password = input("  Enter a dashboard password: ").strip()
    if not password:
        print("  No password entered. Aborting.")
        sys.exit(1)

    confirm = input("  Confirm password: ").strip()
    if password != confirm:
        print("  Passwords don't match. Aborting.")
        sys.exit(1)

    secret_key = secrets.token_urlsafe(32)

    # Read existing docker-compose.yml
    try:
        with open(COMPOSE_FILE, "r") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"  {COMPOSE_FILE} not found. Run this from the project root.")
        sys.exit(1)

    # Remove existing dashboard lines if present
    content = re.sub(r"      - DASHBOARD_PASSWORD=.*\n", "", content)
    content = re.sub(r"      - DASHBOARD_SECRET_KEY=.*\n", "", content)

    # Find the last environment variable line and insert after it
    lines = content.split("\n")
    insert_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("- ") and i > 0:
            # Check if we're in the environment section
            for j in range(i, -1, -1):
                if "environment:" in lines[j]:
                    insert_idx = i
                    break

    if insert_idx is None:
        print("  Could not find environment section in docker-compose.yml")
        sys.exit(1)

    # Find the last env var line
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("- ") and "=" in stripped:
            # Verify it's in the environment block
            insert_idx = i
            break

    # Escape for docker-compose: $ must be $$ to be literal
    compose_password = password.replace("$", "$$")
    new_lines = [
        f"      - DASHBOARD_PASSWORD={compose_password}",
        f"      - DASHBOARD_SECRET_KEY={secret_key}",
    ]

    for offset, line in enumerate(new_lines):
        lines.insert(insert_idx + 1 + offset, line)

    with open(COMPOSE_FILE, "w") as f:
        f.write("\n".join(lines))

    print()
    print("  Dashboard password configured!")
    print()
    print(f"  Password:   {password}")
    print(f"  Secret key: {secret_key}")
    print()
    print("  Added to docker-compose.yml. Restart to apply:")
    print("    docker compose up --build -d")
    print()
    print("  Dashboard: http://localhost:8943/dashboard")
    print()


if __name__ == "__main__":
    main()
