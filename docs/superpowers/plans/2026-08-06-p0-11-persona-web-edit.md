# P0-11 Persona Web Edit Implementation Plan

## Goal

Complete the original P0-11 requirement: the PC Web workspace must load the current owner's persona details and allow editing through the existing versioned API.

## Scope

- Keep the existing persona creation flow and relationship presets.
- Load the first owner persona after session bootstrap when one already exists.
- Reuse the same form for `POST /api/v1/personas` before creation and `PATCH /api/v1/personas/{persona_id}` after creation.
- Expose all currently supported editable persona fields, including relationship label, addresses, description, tone boundaries, and forbidden topics.
- Render server validation errors through the existing status region without unsafe HTML.

## Non-goals

- Persona list management, deletion, multi-persona switching, or backend API changes.
- Import preview, participant mapping, corrections, or any later P0 task.

## Verification

- Add Web contract coverage for PATCH wiring, detail loading, field serialization, and safe text rendering.
- Extend the browser harness with a persona backend flow proving create, reload, and update behavior.
- Run targeted tests, `npm test`, `python -m compileall -q src tests`, `git diff --check`, and CodeGraph sync/status.
