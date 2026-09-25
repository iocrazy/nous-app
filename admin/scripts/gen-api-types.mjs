#!/usr/bin/env node
// Generate admin/src/types/api.generated.d.ts from the backend contract
// snapshot (backend/openapi.json, exported by backend/scripts/export_openapi.py).
//
// Same snapshot and generator as frontend/scripts/gen-api-types.mjs — the admin
// console and the user app read one contract. Both outputs are committed; CI
// regenerates them and fails on any diff (.github/workflows/ci.yml, frontend
// job). Admin code must NOT import the generated file directly — it goes
// through src/types/api.ts, so a backend schema rename touches one place.
//
// Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import openapiTS, { astToString } from 'openapi-typescript';

const here = dirname(fileURLToPath(import.meta.url));
const input = resolve(here, '../../backend/openapi.json');
const output = resolve(here, '../src/types/api.generated.d.ts');

const HEADER = `/**
 * GENERATED — do not edit.
 *
 * Source: backend/openapi.json (regenerate it with
 *   cd backend && uv run python scripts/export_openapi.py)
 * Command: cd admin && npm run gen:api
 *
 * Import through src/types/api.ts instead of this file.
 */

`;

const schema = JSON.parse(await readFile(input, 'utf8'));
const ast = await openapiTS(schema);
await writeFile(output, HEADER + astToString(ast));
console.log(`Wrote ${output}`);
