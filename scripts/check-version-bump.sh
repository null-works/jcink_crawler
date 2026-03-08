#!/usr/bin/env bash
# Pre-commit hook: warns if application code changed but APP_VERSION was not bumped.
# Install: cp scripts/check-version-bump.sh .git/hooks/pre-commit

STAGED=$(git diff --cached --name-only)

# Check if any application code is being committed
CODE_CHANGED=false
for file in $STAGED; do
    case "$file" in
        app/*|scripts/*|cli.py|templates/*)
            CODE_CHANGED=true
            break
            ;;
    esac
done

if [ "$CODE_CHANGED" = false ]; then
    exit 0
fi

# Check if config.py (where APP_VERSION lives) is in the staged changes
if echo "$STAGED" | grep -q "app/config.py"; then
    # Check if APP_VERSION line was actually modified
    if git diff --cached app/config.py | grep -q "^+APP_VERSION"; then
        exit 0
    fi
fi

echo ""
echo "WARNING: You are committing application code but APP_VERSION in app/config.py was not bumped."
echo "         Please increment the version number (MAJOR.MINOR.PATCH) before committing."
echo ""
echo "         To skip this check (e.g. docs-only changes): git commit --no-verify"
echo ""
exit 1
