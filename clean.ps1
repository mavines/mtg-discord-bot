$ErrorActionPreference = "Stop"

Remove-Item -ErrorAction SilentlyContinue cards.db
Remove-Item -ErrorAction SilentlyContinue draft.db
Remove-Item -ErrorAction SilentlyContinue users.db
