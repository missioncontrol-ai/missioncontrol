# Web UI Kluster/Sub-task Display Improvements

## Scope
Kluster: `326cf69b71fb` (`web ui 1.0`)
Mission: `3e8e22e78ab0`

## Implemented in this pass
1. Replaced Explorer's mission-only list + raw JSON flow with a structured mission -> kluster browser.
2. Added status badges (`proposed`, `in_progress`, `blocked`, `done/completed`) for clearer triage.
3. Added kluster-level sub-task metrics in detail view:
   - total sub-tasks
   - in-progress count
   - blocked count
4. Added task cards for sub-tasks (title, status, description, open action).
5. Added lazy node loading for kluster/task details via `fetchNode` to keep payload focused.
6. Auto-selects first mission after tree refresh for faster orientation.

## UX outcomes
- Faster navigation from mission to kluster to sub-task.
- Better status scanning without expanding raw payloads.
- Improved sub-task visibility for active execution planning.

## Proposed next improvements
1. Add status filters and sort modes in Explorer (e.g., show only `blocked` or `in_progress`).
2. Add optimistic inline task status transitions in task cards.
3. Add compact timeline per kluster (recent updates + owner changes).
4. Add keyboard quick-jump between missions/klusters/tasks.
5. Add direct links from matrix events to the related explorer node.

## Changed files
- `src/routes/+page.svelte`
- `src/app.css`

## Verification
- `npm run build` succeeds on this branch after changes.
