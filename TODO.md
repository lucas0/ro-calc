# MVP Tracker — TODO

## Done
- [x] Shared sticky top bar across Refine Calculator and MVP Tracker
- [x] Kill modal: bio3/bio4 specific MVP image & item list update on selection
- [x] Kill modal: Custom Respawn Time button inside Death Time section
- [x] Kill modal: item input accepts ID, looks up name via API, caches result
- [x] Kill modal: "Killed by others" chip in party section (hides loot, mutually exclusive)
- [x] Kill modal: no checkboxes — "didn't drop" selector replaces them
- [x] Kill modal: remove button (✕) on each drop row — persists removal to group config
- [x] Loot tab: per-drop status chips (Sold / For Sale / Kept) with color-coded rows
- [x] Loot tab: sell price shorthand input (5m = 5,000,000 / 500k = 500,000)
- [x] Loot tab: sold items feed payment split calculation in Payments tab
- [x] Loot tab: bio3/bio4 specific MVP name and image
- [x] Loot tab: "↩ Undo" kill button (registerer only) — requires backend restart to work
- [x] Loot tab: "💰 Mark Sold" button only visible to the person who holds the item
- [x] Item DB: ITEM_DB lookup table (item ID → name), resolveItemName helper
- [x] Item DB: persistent /api/items endpoint (items.json) — name + lastSoldPrice + lastSoldAt
- [x] Item DB: last sold price hint shown next to item name in kill modal
- [x] Item DB: item images from divine-pride CDN in kill modal and loot tab
- [x] Item DB: ratemyserver lookup (falls back to divine-pride) for unknown IDs
- [x] Bio3 drop IDs verified from uaRO server (weapons/armor/cards, NOT headgears)
- [x] Bio4 monster IDs corrected to 2235–2241 (iRO wiki), drops verified
- [x] Drop list ordered by drop rate (highest first, card last)
- [x] History tab converted to simple action log

---

## Pending

### Infrastructure (needs backend restart to activate)
- [ ] **Backend restart required** — activates: DELETE /api/kills, GET|PUT /api/items, ratemyserver item lookup
- [ ] Hot-reload backend without manual restart (file watcher in PowerShell loop)

### Item Database
- [ ] Item autocomplete: suggest items from ITEM_DB as user types in the kill modal add-item field
- [ ] Expand ITEM_DB with more commonly dropped items (cards, consumables, common gear)
- [ ] Verify bio3 drop IDs are correct on this server (uaRO may differ from iRO)

### Loot tab — next steps
- [ ] **"All Loot" view** (rename "All Players" filter): compile a single flat list of every item held by every player — grouped by item type with count badge, same grouped-item UX as the per-player view
- [ ] **"Per MVP" view** (new button alongside "All Loot"): show what is currently shown by "all players" — each MVP kill as a card. Make each kill card individually collapsible (click header to expand/collapse drop rows). Default: expanded if it has active items, collapsed if fully sold.
- [ ] Export payment summary — copy to clipboard or send via Discord webhook
- [ ] Filter loot tab by date range or by specific MVP
- [ ] Sell-pool: track which group member performed the actual sale
- [ ] Kill modal: bulk "mark all as sold" with one price for identical items
- [x] Remember last sold price per item ID — pre-fill the price input in the "Mark Sold" modal with the last known price for that item
- [x] Loot tab: group/sort drop rows so same items (same item ID) appear together
- [x] Mark Sold ordering: when a player holds multiple of the same item across different kills, mark the oldest kill's item as sold first (FIFO by kill date)

### Payments tab — next steps
- [ ] **Remove item rows from player cards** — payments tab should show money only, not item lists
- [ ] **Estimated value panel**: show estimated value of items still being held or listed for sale (yellow card, already exists) — keep but make it the only place items are mentioned
- [ ] **Zeny held per player**: show how much zeny each player is currently holding from sold items (money they need to pay out or that is owed to them)
- [ ] **Simplified "who pays whom" view**: replace the current per-player balance cards with a clear settlement table — one row per transfer ("Player A → Player B: X z"), no redundant math visible
- [ ] **"Mark as Paid" button**: on each transfer row, allow settling a debt between two players. Marking as paid reduces the "money available" amount and removes that transfer from the settlement list. This is the final step of the loot cycle — from kill → drop → sell → split → paid.
- [ ] Transaction simplification: A→B + B→C should collapse to A→C (net balances already handle this — verify with 3+ player chains and document)

### Undo Kill — follow-up
- [ ] Log undo-kill action and alert party members who were part of that kill (Discord webhook or in-app notification)

### Player Colors
- [ ] Assign a unique color per group member (stored in serverConfig, configurable)
- [ ] Member chips in kill modal tinted with player color when selected
- [ ] Loot items kept by a player use that player's color (left border + row tint)
- [ ] Payment section uses player color for the member header/badge

### Group & Members
- [ ] Group invite link UI (invite code already exists in backend)
- [ ] **Member removal by admin** — remove a member from the group (admin only, log the action)
- [x] Discord webhook spawn notifications — backend sends automatically every 60 s (no browser required); 🔔 Notify Discord button on MVP cards; rich embed with thumbnail + /navi command

### Log / History tab
- [ ] **Change log section** — show recent changes (edits to kills, item status changes) alongside kill registrations and payment events
- [ ] **Solo group auto-create** — when a user registers, automatically create a solo group named after their character so they can use the tracker solo without joining a group

### Groups
- [ ] **Solo group display name** — show the member's character name as the solo group name instead of "Solo"

### Kill Modal
- [ ] Autocomplete for the "add item" field using ITEM_DB + itemDb

---

## Notes
- Item image URLs: `https://static.divine-pride.net/images/items/item/{id}.png`
- Monster image URLs: `https://static.divine-pride.net/images/mobs/png/{mid}.png`
- Authoritative drop source: `https://my.uaro.kiev.ua/monster_new/view/?id={mid}` (uaRO server)
- Fallback drop source: `https://db.irowiki.org/db/monster-info/{id}/` (iRO wiki)
- Item name lookup: ratemyserver first, divine-pride fallback
- Backend data dir: `B:\Coding\ro-calc\mvp-tracker\data\`
- kills stored per group: `kills_{groupId}.json`
- items DB (shared): `items.json`
