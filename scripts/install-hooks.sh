#!/bin/sh
# À lancer une fois après le clone : active les hooks versionnés du dépôt.
git config core.hooksPath scripts/hooks
echo "hooks activés (core.hooksPath=scripts/hooks)"
