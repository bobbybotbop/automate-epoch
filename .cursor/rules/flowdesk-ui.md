# FlowDesk — step editor dropdown conventions

When building or changing **Automations** step editors (or similar UI that binds to on-disk config):

1. **Target image** — Use a `QComboBox` populated from `targets_dir.glob("*.png")` (sorted). If the saved value is missing from the list, append it so existing automations still load (`_set_combo` pattern).

2. **Parser / loop variables** — Use a `QComboBox` populated from all `rule_name` values across `rules/*.json` (sorted). Include an empty first row for “none” on optional fields (e.g. click **Loop variable**). Use the same `_set_combo` pattern for values not in the list.

3. **Type text with `{{variable}}`** — Prefer a variable dropdown (same rule names as above) that **inserts** `{{rule_name}}` at the cursor into the value field, rather than only free-typed placeholders—unless the design explicitly needs arbitrary text with no picker.

4. **Refresh** — Repopulate these combos when the automation file is selected and when the Automations tab is shown, so new targets/rules appear without restarting the app.

Agents adding new “pick a target” or “pick a parsed field” inputs should follow the same pattern and keep lists in sync with the filesystem.
