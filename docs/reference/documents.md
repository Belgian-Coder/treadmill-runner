---
title: Documents
type: reference
status: active
owner: document-artifacts
audience: agent
updated: 2026-05-27
---

# Documents

Route attachments before optional local AI.

```shell
python -B .agents/manage.py attachment-route --file <path>
python -B .agents/manage.py attachment-route --file <path> --write-plan evidence/attachments
```

Owners: PDF=`document-artifacts`, DOCX=`document-artifacts`, XLSX=`document-artifacts`, PPTX=`document-artifacts`; images/logs/archives use attachment routing plus follow-up.

Evidence should include JSON/Markdown, hashes, safety findings, asset inventory, relevant compares, and next commands.
