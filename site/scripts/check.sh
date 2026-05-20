#!/usr/bin/env bash
# Pre-deploy gate. Anything you ship should pass this end-to-end.
set -euo pipefail

cd "$(dirname "$0")/.."

PM=${SITE_PM:-bun}

echo "▶ lint"
$PM run lint

echo "▶ typecheck"
$PM run check

echo "▶ unit tests"
$PM run test

echo "▶ build"
$PM run build

echo "▶ e2e"
$PM run test:e2e

echo "✓ site/scripts/check.sh complete"
