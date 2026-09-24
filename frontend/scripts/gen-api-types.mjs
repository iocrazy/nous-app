#!/usr/bin/env node
// Generate frontend/types/api.generated.d.ts from the backend contract snapshot
// (backend/openapi.json, exported by backend/scripts/export_openapi.py).
//
// Both files are committed; CI regenerates this one and fails on any diff
// ("Generate API types & diff" in .github/workflows/ci.yml). Business code
// must NOT import the generated file directly — import the named aliases in
// types/api.ts, so a backend schema rename touches one line.
//
// Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import openapiTS, { astToString } from 'openapi-typescript';

const here = dirname(fileURLToPath(import.meta.url));
const input = resolve(here, '../../backend/openapi.json');
const output = resolve(here, '../types/api.generated.d.ts');

const HEADER = `/**
 * GENERATED — do not edit.
 *
 * Source: backend/openapi.json (regenerate it with
 *   cd backend && uv run python scripts/export_openapi.py)
 * Command: cd frontend && npm run gen:api
 *
 * Import the aliases in types/api.ts instead of this file.
 */

`;

const schema = JSON.parse(await readFile(input, 'utf8'));
const ast = await openapiTS(schema);
await writeFile(output, HEADER + astToString(ast));
console.log(`Wrote ${output}`);
