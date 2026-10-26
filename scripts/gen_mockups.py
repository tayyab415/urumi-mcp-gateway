#!/usr/bin/env python3
"""Generate UI mockups for the Urumi dashboard with OpenAI's image model.

Style comes from intended_design.md. Reference frames from the design video are
attached to every call so the model copies the component look (card, pills, orb,
progress segments) and not just the hex values.

Usage:
  python scripts/gen_mockups.py --env-file /path/to/.env            # all screens
  python scripts/gen_mockups.py --env-file ... --only 01 05 --force  # some screens
  python scripts/gen_mockups.py --list

The key is read from OPENAI_API_KEY or from --env-file. It is never written to disk here.
"""
import argparse, base64, concurrent.futures as cf, json, os, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "mockups"
REFS = [ROOT / "docs/design-reference/agent-status/frames" / n
        for n in ("03-running.png", "05-needs-approval.png", "09-done.png")]
REFS_CHAT = [ROOT / "docs/design-reference/chat-agent/frames" / n
             for n in ("04-three-steps.png", "06-needs-review.png", "07-diff-open.png", "01-docked-assistant-empty.png")]
MODEL = "gpt-image-2.5-sunburst"
SIZE = "1536x1024"

STYLE = """
Produce a high-fidelity UI mockup: a flat 2D screenshot of a web app. No device frame, no browser chrome, no perspective, no scene, no watermark, no lorem ipsum, no emojis, no illustrations, no stock photos.

VISUAL LANGUAGE. The attached reference screenshots show the component style to copy: card, pill buttons, status orb, segmented progress bar, row styling and type. Copy their styling only. Do not copy their layout or their text.
- Canvas #121212. Cards #181818 with a 1px #2b2b2b hairline border, 20px corner radius and a soft dark shadow underneath. No gradients, no glass, no glow.
- Font Inter. Body 13px, small meta text 11px, page titles 17px medium. Text colors: primary #fafafa, secondary #bbbbbb, tertiary #919191, in-progress text #858585.
- One accent blue #6aa0fd, used only for progress fill, the single primary button of a view, and "needs you" markers. Success green #56ce7f only for done rings and checks. Danger red #f87171 and warning amber #f5b84a only for failed and unknown states.
- Icons are Lucide-style outline icons, 16px, 1.5px stroke.
- Buttons are fully rounded pills, 32px tall. Secondary: #181818 fill, 1px #2b2b2b border, #fafafa label with a leading icon. Primary: #6aa0fd fill with a near-black label. Ghost: plain #bbbbbb text with a trailing arrow-up-right icon.
- Status orb: a 22px circle with a thin gray ring and a small coral 12-ray starburst in the middle. Running shows a white arc on the ring. Done is a full green ring with a green check. Needs approval shows a small blue dot at the orb's top right. Failed is a red ring with an x. Unknown is an amber ring. Disabled is a dim gray ring with no glyph.
- Segmented progress bar: thin 3px fully rounded segments with 3px gaps. Complete is #6aa0fd, active is #3b4354 (partly filled with #6aa0fd), pending is #2b2b2b.
- State is shown by text color and verb tense, not by badges. Live rows are present tense in dim gray with a whole-second duration. Finished rows are past tense in lighter gray with a one-decimal duration such as 1.9s, right-aligned in tertiary gray.
- Selection is neutral, never blue. A selected tab, segmented option, list item or toggle option uses a #242424 fill with #fafafa text. User chat bubbles are #242424 too. Blue never marks selection.
- Row height 26px, card padding 12px. Quiet and restrained, lots of dark space, at most one filled primary button per view.
- App shell, ONLY when the SCREEN description says the page has the rail: a 232px slim left rail on the canvas color. At its top, a small coral starburst mark and the wordmark "Urumi". Nav items with icons: Overview, Servers, Chat, Team, API keys, Audit log, Permissions, Stores. The active item has a #181818 fill. At the bottom of the rail, a 28px initials avatar with the user's name and role in tertiary gray.
- Fill the whole 3:2 frame like a real 1440x960 desktop screen. When there is a rail, the content column fills the space right of it with 40px padding. Do not leave big empty areas.
- Every piece of text must be spelled exactly as given below. Do not add other text.
"""

DATA = """
SHARED SAMPLE DATA (use these exact names). Team: Maya Chen (admin, maya@urumi.test), Omar Reyes (member, omar@urumi.test), Priya Nair (member, invited, priya@urumi.test). MCP servers: "woo" = WooCommerce store, "shipping" = Shipping quotes, "supplier" = Supplier catalog, "analytics" = Analytics. Tool names look like woo__list_orders. Orders: #1043 Linen tote bag x4 $86.00 Processing, #1042 Ceramic mug x2 $34.00 Completed, #1041 Canvas apron x1 $29.00 Processing. Model: Claude Haiku 5.5. Gateway URL: https://gateway.urumi.test/mcp.
"""

SCREENS = [
("00", "login", """
SCREEN: Sign in. No left rail. A single 360px card centered on the canvas. Above it the coral starburst mark and the wordmark "Urumi" with the line "Team MCP gateway" in tertiary gray. Inside the card: a label "Email" with an input showing "maya@urumi.test", a label "Password" with a masked input, then one full-width primary pill button "Sign in". Under the card a tertiary line: "Ask an admin for an invite". Nothing else on the screen.
"""),
("01", "servers", """
SCREEN: Servers (admin view, signed in as Maya Chen, Admin). Rail item "Servers" is active.
Page header: title "Servers", subtitle in tertiary gray "3 of 4 enabled. Tools are shared with your team as <server>__<tool>." At the right, a primary pill button with a plus icon: "Add server".
Below, four full-width server cards stacked with 12px gaps.
Card 1, expanded. Header row: green check ring orb, "WooCommerce store" in primary text, URL "woo-mcp.urumi-store.svc:8080/mcp" in tertiary, and "Connected" on the right. Meta row: left "woo" in tertiary, right "5 tools . p50 118 ms". Then 5 tool rows, each with a 16px icon, the tool name in secondary gray, a short description in tertiary gray, and a duration on the right: woo__list_products "List products" 1.1s; woo__create_product "Create a product" 2.4s; woo__list_orders "List orders" 1.2s; woo__get_order "Get one order" 0.8s; woo__update_order_status "Change an order's status" 1.6s. Action bar: secondary pills "Disable" (pause icon), "Edit" (pencil icon), "Remove" (trash icon), and at the right the ghost link "Open audit".
Card 2, collapsed to just its header row: green check ring orb, "Shipping quotes", "shipping . 3 tools" in tertiary, "Connected" on the right.
Card 3, expanded, unreachable. Header row: red ring orb with an x, "Supplier catalog", "Unreachable" on the right. First row in danger style, red x icon: "Connection refused to supplier.demo.svc:8080" with "last healthy 2m ago" on the right. Second row dim: "Retrying in 12s". Then 4 dimmed tool rows: supplier__list_products, supplier__get_stock, supplier__get_price, supplier__restock. Action bar: secondary pills "Retry now" (refresh icon), "Edit", "Disable", and the ghost link "Open audit".
Card 4, collapsed, disabled: dim gray ring orb, "Analytics" in tertiary gray, "Disabled" on the right, and a ghost link "Enable".
"""),
("02", "server-add-panel", """
SCREEN: Add server. The Servers page is visible but dimmed to about 40% behind a dark overlay, with the rail on the left. Behind the overlay there are only three collapsed server cards: "WooCommerce store" with a green check ring orb and "5 tools", "Shipping quotes" with a green check ring orb and "3 tools", and "Analytics" with a dim gray ring and "Disabled". No supplier card exists yet. On the right, a 460px side sheet (#181818, 1px #2b2b2b left border, 20px radius on its left corners) slides over it.
Sheet content, top to bottom: title "Add MCP server" with an x icon button at the right. Label "Name" with an input "Supplier catalog". Label "Prefix" with an input "supplier" and below it in tertiary gray: "Tools will appear as supplier__<tool>". Label "URL" with an input "https://supplier.demo.svc:8080/mcp". Label "Authentication" with a three-option segmented control: "None", "Bearer token" (selected, #242424 fill), "Custom header". Label "Token" with a masked input "................" and below it in tertiary gray: "Stored encrypted on the server. Never shown again and never sent to clients."
Then a result card in step-log style after pressing "Test connection": header row green check ring orb and "Connected in 212 ms". Rows: "Reached the server" 0.2s; "Found 4 tools" 0.1s; and an amber row with an alert-triangle icon: "supplier__list_products and woo__list_products share a base name. Both stay available under their own prefix."
Footer of the sheet: secondary pill "Test connection" on the left, then secondary pill "Cancel" and one primary pill "Add server" on the right.
"""),
("03", "team-and-keys", """
SCREEN: Team (admin view, Maya Chen). Rail item "Team" is active.
Header: title "Team". Below it a two-option tab control: "Members" (selected, #242424 fill, white text, not blue) and "API keys". At the right a primary pill button with a plus icon: "Invite member".
Card "Members" with three rows. Each row has a 28px initials avatar, name and email, a role on the right shown as a small outlined pill ("Admin" or "Member"), and "last active" in tertiary gray. Maya Chen, maya@urumi.test, Admin, "active now". Omar Reyes, omar@urumi.test, Member, "active 4m ago". Priya Nair, priya@urumi.test, Member, "invited, not joined".
Card "API keys" with three rows in step-log style. Row 1 is in the middle of a confirm: key icon, "urumi_7f3a...c91e" in monospace, "Omar Reyes . created 2 days ago . used 4m ago" in tertiary, and on the right two pills: a primary pill "Confirm revoke" and a secondary pill "Cancel". Row 2: key icon, "urumi_21bd...09aa", "Maya Chen . created 5 days ago . used just now", and a secondary pill "Revoke". Row 3 is dimmed: x icon, "urumi_90ce...55f1", "Omar Reyes . Revoked 2m ago".
Under the cards, one tertiary line: "Keys are stored hashed. A key is shown once, when it is created."
"""),
("04", "connect-client-member", """
SCREEN: Connect (member view, signed in as Omar Reyes, Member). The rail shows only: Chat, Connect (active), Audit log. No admin items.
Header: title "Connect", subtitle in tertiary "Use one URL and your own key. You never see the servers' credentials."
Left column (about 60% width). Card "Gateway URL" with a monospace field "https://gateway.urumi.test/mcp" and a secondary pill "Copy". Card "Your API key": a monospace field "urumi_k9Zq3vT8mXw2LpR4nB7c" and a secondary pill with a green check "Copied", and below it a tertiary line "Shown once. Save it now." Card "Add it to your client": a tab control "Claude Code" (selected, #242424 fill with white text, not blue), "Cursor", "MCP Inspector", and under it a code block in monospace on #121212 with the text: claude mcp add --transport http urumi https://gateway.urumi.test/mcp --header "Authorization: Bearer urumi_k9Zq3vT8mXw2LpR4nB7c".
Right column (about 40%). Card "Tools you can use" with the line "9 tools from 3 servers" in tertiary and a grouped list. Each group has a green check ring orb (all three identical, no spinning arcs), the server name, and "3 tools" on the right, then its three tool names in tertiary gray. woo: woo__list_products, woo__list_orders, woo__get_order. shipping: shipping__get_quote, shipping__list_carriers, shipping__track_parcel. supplier: supplier__list_products, supplier__get_stock, supplier__get_price. Below the list, a tertiary line "Restricted tools are hidden from you."
"""),
("05", "audit-log", """
SCREEN: Audit log (admin view). Rail item "Audit log" is active.
Header: title "Audit log". Right side of the header: a small orb with a spinning white arc and the text "Live". Under the header a row of secondary pills with chevrons: "All users", "All servers", "All outcomes", "Last 24 hours".
One wide card holding a list. Column labels in tertiary 11px: "Event", "User", "Via", "When", "Took", "Outcome". Eight rows, each 26px tall with a 16px icon:
1. (faint blue tint, newest) file-pen icon, "Created product Linen tote bag", woo__create_product in small tertiary text under it, Maya Chen, chat, 12s ago, 2.3s, green check "Success".
2. search icon, "Listed 3 orders from today", woo__list_orders, Omar Reyes, mcp, 41s ago, 1.2s, green check "Success".
3. search icon, "Listed 3 orders from today", woo__list_orders, Maya Chen, chat, 3m ago, 1.1s, green check "Success".
4. truck icon, "Got 2 shipping quotes", shipping__get_quote, Omar Reyes, mcp, 4m ago, 0.9s, green check "Success".
5. shield-off icon, "Rejected. API key revoked", Omar Reyes, mcp, 6m ago, 4ms, red x "Refused".
6. search icon, "Supplier unreachable", supplier__list_products, Maya Chen, chat, 9m ago, 10.0s, red x "Failed".
7. file-pen icon, "Timed out. Check the order before retrying", woo__update_order_status, Maya Chen, chat, 12m ago, 15.0s, amber circle with a question mark "Outcome unknown".
8. search icon, "Listed 5 products", woo__list_products, Omar Reyes, mcp, 20m ago, 1.1s, green check "Success".
Durations right-aligned with tabular digits. At the bottom of the card a ghost link "Load older".
"""),
("06", "chat-tool-run", """
SCREEN: Chat (signed in as Maya Chen). Rail item "Chat" is active. Inside the content area, a 240px conversation list on the left with a secondary pill "New chat" and three items under the tertiary label "Today": "Orders today" (selected, #181818 fill), "Create tote bag", "Stock check". The chat column is on the right, about 700px wide, centered in the remaining space.
Conversation. A user message, right-aligned, in a #242424 rounded bubble: "What orders came in today? Get a shipping quote for the biggest one."
Then an agent run card, full chat-column width, finished. Header row: green check ring orb and the text "Done in 4s . 3 tools" in primary text, no timer. Meta row: left "Claude Haiku 5.5 . acting as Maya Chen", right "3 of 3 steps". Three complete blue segments. Three rows: search icon "Found 3 orders today" 1.2s; file-search icon "Read order #1043" 0.8s; truck icon "Got 2 shipping quotes for order #1043" 0.9s. Action bar with only the ghost link "Open run".
Then the assistant's answer as plain text, no bubble: "Three orders came in today. The largest is #1043. Two carriers quoted it." Below it a compact table inside a card: "#1043 . Linen tote bag x4 . $86.00 . Processing"; "#1042 . Ceramic mug x2 . $34.00 . Completed"; "#1041 . Canvas apron x1 . $29.00 . Processing". Then two small outlined quote chips: "Standard, 3 to 5 days, $6.40" and "Express, 1 to 2 days, $14.90".
At the bottom of the chat column, a pill composer with placeholder "Ask about your store" and a round primary send button with an arrow-up icon.
"""),
("07", "chat-approval-gate", """
SCREEN: Chat approval gate (signed in as Maya Chen). Same layout as a chat page: rail with "Chat" active, a 240px conversation list with a secondary pill "New chat" and, under the tertiary label "Today", exactly three items: "Orders today" (selected, #181818 fill, no blue border), "Create tote bag", "Stock check". No timestamps, no message counts, and a centered 700px chat column.
Conversation. User bubble, right-aligned, #242424 fill (not blue): "Mark order #1043 as completed." Assistant plain text: "I can do that. It changes the order, so I need your OK first."
Then an agent run card in the needs-approval state, full chat-column width. Header row: orb with a small blue dot at its top right and no spinning arc, a terminal icon, the headline "Needs your approval to change order #1043 to completed" in bright #fafafa, and a timer "0:03" on the right. Meta row: left "Claude Haiku 5.5 . acting as Maya Chen", right "Step 2 of 2". Two segments: the first complete blue, the second in the active #3b4354 tone. Two rows: search icon "Found order #1043" 0.8s; then a row with a blue terminal-square icon and bright text "Waiting for approval to change order #1043 to completed" 0s. Action bar: a primary pill "Approve" with a check icon, a secondary pill "Deny" with an x icon, and at the right the ghost link "Open run". The Approve button is the only filled button on the screen. The mouse cursor is drawn resting on the Approve button.
The composer at the bottom is shown dimmed with placeholder "Waiting for your decision".
"""),
("08", "permissions", """
SCREEN: Permissions (admin view). Rail item "Permissions" is active.
Header: title "Permissions", subtitle in tertiary: "A tool a role cannot use is hidden from its tool list and rejected if called directly."
One wide card with a table. Column labels in tertiary 11px: "Tool", "Admin", "Member". Each cell in the role columns is a small toggle switch: on is a light gray #bbbbbb track with a dark knob, off is a #2b2b2b track with a gray knob. Three groups with small group headings that each have a small orb. Group "woo": woo__list_products (Admin on, Member on), woo__create_product (Admin on, Member off), woo__list_orders (on, on), woo__get_order (on, on), woo__update_order_status (on, off). Group "shipping": shipping__get_quote (on, on), shipping__list_carriers (on, on), shipping__track_parcel (on, on). Group "supplier": supplier__list_products (on, on), supplier__get_stock (on, on), supplier__get_price (on, on), supplier__restock (on, off). Each tool name has a short description in tertiary gray.
Floating at the bottom center of the content area, a pill bar on #181818 with a border: "3 unsaved changes", a ghost link "Discard", and a primary pill "Save".
"""),
("09", "stores-provisioning", """
SCREEN: Stores (bonus multi-store provisioning, admin view). Rail item "Stores" is active.
Header: title "Stores", subtitle in tertiary "3 of 5 stores used". A primary pill with a plus icon at the right: "Create store".
Three full-width store cards stacked.
Card 1, ready: green check ring orb, "Linen and Co" in primary text, "shop-a.urumi.test" in tertiary, "Ready" on the right. Meta row: left "Created 3 days ago", right "14 products . 38 orders". Action bar: secondary pills "Storefront" (arrow-up-right icon), "WP admin" (arrow-up-right icon), "Delete" (trash icon), and a ghost link "Open audit".
Card 2, provisioning, expanded as an agent run card: orb with a spinning white arc, a database icon, the shimmering headline "Installing WooCommerce" in dim gray, a timer "0:42". Meta row: left "Atlas Coffee . shop-b.urumi.test", right "Step 4 of 6". Six segments: three complete blue, the fourth active and half filled, two pending. Rows: box icon "Created namespace" 0.6s; database icon "Database ready" 9.8s; globe icon "WordPress started" 14.2s; shopping-cart icon "Installing WooCommerce" 12s (live, brighter icon). Action bar: a secondary pill "Cancel" with an x icon.
Card 3, failed: red ring orb with an x, "Demo Sneakers", "shop-c.urumi.test", "Failed" on the right. Row in danger style: red x icon "Failed at step 2. Database volume was not bound within 120s." Action bar: secondary pills "Retry" (refresh icon), "Delete" (trash icon), and the ghost link "Open run".
"""),
("10", "overview-observability", """
SCREEN: Overview (admin view). Rail item "Overview" is active.
Header: title "Overview", subtitle in tertiary "Last 24 hours".
Top: a full-width alert card. Red ring orb with an x, headline "Supplier catalog is unreachable", then a line in tertiary gray "Connection refused to supplier.demo.svc:8080. Last healthy 2 minutes ago. Calls to it fail fast and the other servers keep working." Action bar: secondary pill "Retry now", ghost link "Open server".
Middle: a row of three equal cards, one per server. Each has a small orb, the server name, and three lines: calls, error rate, latency. woo: green check orb, "woo", "1,204 calls", "0.4% errors", "p50 118 ms . p95 640 ms". shipping: green check orb, "shipping", "311 calls", "0.0% errors", "p50 92 ms . p95 210 ms". supplier: red x orb, "supplier", "87 calls", "63% errors", "p50 timeout".
Bottom: two cards side by side. Left, about 62% width, "Tool calls per hour": a clean bar chart with 24 bars in one blue #6aa0fd, the last bar full blue and earlier bars at 60% opacity, a few tertiary axis labels ("00:00", "06:00", "12:00", "18:00", "now") and a light horizontal gridline. Right, "Slowest tools (p95)": a list of five rows, name left, value right: woo__update_order_status 2.7s; woo__create_product 2.4s; supplier__list_products 10.0s; woo__list_orders 1.4s; shipping__get_quote 0.9s.
"""),
("11", "run-card-states", """
SCREEN: A component sheet titled "Agent run card . states" (17px medium) at the top left of a #121212 canvas, with no app rail. Below it a 4 by 2 grid of run cards, each 360px wide, each with a tertiary caption underneath. Align cards to the top of each grid cell.
1 caption "Collapsed pill, running": a 36px tall pill with the spinning-arc orb, a file-search icon, "Reading 12 files" and "0:00".
2 caption "Expanded, running": header orb with arc, search icon, shimmering dim headline "Finding orders for today", timer "0:04". Meta "Claude Haiku 5.5" and "Step 3 of 6". Six segments: two complete, third active half filled, three pending. Rows: "Listed 5 products" 1.1s, "Found 3 orders today" 1.2s, then live "Reading order #1043" 1s. Buttons "Pause", "Stop", ghost "Open run".
3 caption "Needs approval": blue dot on the orb, headline "Needs your approval to change order #1043" bright, buttons "Approve" (primary) and "Deny".
4 caption "Done": green ring with check, headline "Done in 4s . 3 tools" and NO timer and no step icon, meta "3 of 3 steps", all segments blue, only "Open run".
5 caption "Paused": frozen arc, headline "Paused at step 3", buttons "Resume" (play icon) and "Stop".
6 caption "Denied": no arc, headline "Stopped. You denied the change", last row "Denied: change order #1043" with an x in tertiary gray, only "Open run".
7 caption "Failed": red ring with an x, headline "Failed at step 2", row in danger style "Supplier unreachable", buttons "Retry" and ghost "Open run".
8 caption "Outcome unknown": amber ring, headline "Not sure the change applied", row with amber icon "Timed out. Check the order before retrying", only "Open run".
For cards 3 to 8 show an expanded body with about 3 rows each, kept short.
"""),
("12", "motion-expand", """
SCREEN: A motion storyboard on a plain #121212 canvas. NO left rail, NO sidebar, NO wordmark, NO app shell, NO user avatar. The board fills the whole frame edge to edge. Title at top left "Pill to card . expand" (17px medium) and under it in tertiary "Spring that settles in about 330 ms. No bounce. The header row never moves."
A grid of 5 large equal panels on a #0c0c0c band: three panels in the top row, two in the bottom row (the bottom two are left-aligned, and the empty third cell holds the 4 annotation callouts as a short list). Each panel is a #121212 rectangle about 440px wide and 340px tall with a thin border, the same component drawn at the same large scale and top-aligned inside it. Under each panel a time label in tertiary: "0 ms", "60 ms", "120 ms", "220 ms", "400 ms". Thin arrows between frames.
Frame 1: just the pill (a short wide rounded pill, 36px tall) with the spinning-arc orb, a file-search icon, "Reading 12 files", "0:00".
Frame 2: the same pill slightly wider and taller, the bottom edge starting to drop, a faint ghost outline of the final card size drawn dashed behind it.
Frame 3: about half grown. The header row is unchanged at the top. A first faint line of the body, "Claude Haiku 5.5", is fading in.
Frame 4: almost full size, body content mostly visible: meta row, six thin segments, one row "Reading 12 files", the action bar.
Frame 5: the final settled card, 360px wide, with the header row, meta row "Claude Haiku 5.5" and "Step 1 of 6", six segments (first partly filled), row "Reading 12 files" 1s, pills "Pause" and "Stop", ghost "Open run".
The 4 short annotation callouts, as a tertiary-gray list in the empty cell: "Header stays pinned", "Grows from top center", "Body fades in behind a clip", "Settles, no overshoot".
"""),
("13", "motion-step-change", """
SCREEN: A motion storyboard on a plain #121212 canvas. NO left rail, NO sidebar, NO wordmark, NO app shell, NO user avatar. The board fills the whole frame edge to edge. Title top left "Step change . text roll" (17px medium) and under it in tertiary "Words move fast (about 100 ms). The box settles slower (about 330 ms)."
A grid of 5 large equal panels on a #0c0c0c band: three in the top row, two in the bottom row, and the empty third cell of the bottom row holds the 4 annotation callouts as a short list. Each panel is about 440px wide and 340px tall and shows the same cropped part of the run card at the same scale: header row, meta row, segment bar, and the last two log rows. Time labels under the panels: "0 ms", "40 ms", "80 ms", "120 ms", "330 ms". In every panel the two log rows are exactly "Found 4 call sites" 1.5s, then the step row. Timer in the header is "0:05" in every panel.
Panel 1 (0 ms): header icon file-pen, dim headline "Editing src/billing/invoice.ts". Meta "Claude Haiku 5.5" and "Step 3 of 6". Segments: two complete blue, third in the active tone, three pending. Rows: "Found 4 call sites" 1.5s; "Editing src/billing/invoice.ts" 2s in dim text with a bright icon.
Panel 2 (40 ms): the old headline "Editing src/billing/invoice.ts" has moved up a few pixels and is faded and blurred; the new headline "Updating 5 test files" with a files icon is entering from below, overlapping it. Last row is cross-fading from "Editing src/billing/invoice.ts" 2s to "Edited src/billing/invoice.ts" 2.5s. Segment 3 begins to fill blue.
Panel 3 (80 ms): headline "Updating 5 test files" in place, old text gone. Last row "Edited src/billing/invoice.ts" 2.5s in lighter gray. Segment 3 about 60% filled blue, segment 4 switched to the active tone.
Panel 4 (120 ms): segment 3 fully blue. A new row "Updating 5 test files" 0s is fading in about 8px below its final place at 50% opacity, and the card's bottom edge has dropped one row lower than in panel 3.
Panel 5 (330 ms): settled. Meta reads "Step 4 of 6". Rows: "Found 4 call sites" 1.5s; "Edited src/billing/invoice.ts" 2.5s; "Updating 5 test files" 0s. A soft light band (shimmer) is crossing the headline.
The 4 short callouts, as a tertiary-gray list in the empty cell: "1 Old text rolls up, fades, blurs", "2 New text rolls in from below", "3 Tense changes in place, 2s becomes 2.5s", "4 Shimmer starts 0.4 s later".
"""),
("14", "motion-approval-morph", """
SCREEN: A motion storyboard on a plain #121212 canvas. NO left rail, NO sidebar, NO wordmark, NO app shell, NO user avatar. The board fills the whole frame edge to edge. Title top left "Approval gate . button morph and press" (17px medium) and under it in tertiary "Same two button slots change role. Open run never moves."
A 2 by 3 grid of 6 panels on a #0c0c0c band. Each panel shows the same cropped bottom part of a run card at the same scale: the last row and the action bar, plus the header orb in a small inset at its top left. Time labels under each panel.
Panel 1 "0 ms": row "Running tests 12 of 12"; buttons "Pause" and "Stop"; ghost "Open run". Orb has a spinning white arc.
Panel 2 "70 ms": the buttons are mid-morph: Pause is tinting blue, its label is blurred and cross-fading with "Approve"; Stop is cross-fading with "Deny". The orb's arc is fading out and a blue dot is scaling in.
Panel 3 "150 ms": row reads "Waiting for approval to run git push" with a blue icon; buttons "Approve" (blue, check icon) and "Deny" (outlined, x icon); orb has a blue dot and no arc.
Panel 4 "hover": the same, a mouse cursor over Approve, the blue slightly darker (#6396ea).
Panel 5 "press": Approve is scaled down to 96% and darker (#5d8cdb), cursor on it.
Panel 6 "resume, +100 ms": buttons are back to "Pause" and "Stop" with the cursor now over Pause, which shows a subtle #242424 hover fill; row reads "Pushing fix/proration-rounding" 0s; the orb has its spinning arc again.
The 4 short callouts, as a tertiary-gray list in the empty cell or under the grid: "Blur crossfade, widths animate", "Blue dot pops in", "Press scales to 0.96", "Timer on this row restarts at 0s".
"""),
("15", "motion-done", """
SCREEN: A motion storyboard on a plain #121212 canvas. NO left rail, NO sidebar, NO wordmark, NO app shell, NO user avatar. The board fills the whole frame edge to edge. Title top left "Run complete . ring close and check" (17px medium) and under it in tertiary "One green ring is the only celebration. The card keeps its height."
A grid of 5 large equal panels on a #0c0c0c band: three in the top row, two in the bottom row, and the empty third cell of the bottom row holds the 4 annotation callouts as a short list. Each panel is about 440px wide and 340px tall. Time labels above or under the panels: "0 ms", "100 ms", "200 ms", "300 ms", "500 ms". Each panel shows the same cropped run card: header row, meta row, segment bar, the last log row, and the action bar strip.
Panel 1 (0 ms): orb with a white spinning arc and a coral starburst; terminal icon and dim headline "Pushing fix/proration-rounding"; timer "0:13". Meta "Claude Haiku 5.5" and "Step 6 of 6". Six segments, the last about 90% filled blue. Last row: "Pushing fix/proration-rounding" 1s, dim text, bright icon. Buttons "Pause", "Stop", ghost "Open run".
Panel 2 (100 ms): the orb's arc has turned green and extends around most of the ring, coral starburst still visible. The headline is cross-fading between "Pushing fix/proration-rounding" and "Done in 13s . 6 files changed". Timer "0:13" at 50% opacity. Last segment fully blue. Last row cross-fading to "Pushed fix/proration-rounding" 1.4s. Pause and Stop at 60% opacity, Open run at full opacity.
Panel 3 (200 ms): the ring is a full green circle, the starburst has faded and a small green check is scaling up inside. The terminal icon is gone and the headline "Done in 13s . 6 files changed" is sliding left. NO timer. Meta reads "6 of 6 steps". Last row "Pushed fix/proration-rounding" 1.4s. Pause and Stop at 25% opacity, Open run at full opacity.
Panel 4 (300 ms): settled orb, green ring with a full-size green check. Headline "Done in 13s . 6 files changed" in bright text right next to it. NO timer, NO step icon. Meta "6 of 6 steps". All six segments blue. Last row "Pushed fix/proration-rounding" 1.4s. Only the ghost link "Open run" in the action bar, at the same place as in panel 1; the space where Pause and Stop were is empty.
Panel 5 (500 ms): identical to panel 4 (the final resting state), to show that nothing else moves.
The 4 short callouts, as a tertiary-gray list in the empty cell: "1 Arc turns green and closes", "2 Check pops in with a fast spring", "3 Headline slides left", "4 Open run does not move".
"""),
]


CHAT_STYLE = """
CHAT AGENT RUN CARD. For this screen the attached reference frames show the run card and the docked assistant. Copy their styling exactly, not their text.
- The run card is one rounded card, 24px radius, fill #171717, on a canvas of #1f1f1f (inside a full page it sits on the page color with a 1px #2b2b2b border). No orb and no segmented progress bar.
- Header: the task title at top left (16px, medium, #f5f5f5), a small circular replay icon at top right (#ababab). Under it the model line: a small coral 12-ray starburst and the model name (12px, #adadad) at the left; at the right the status text with a small spinner ("Working", #a6a6a6) and the elapsed timer in tabular digits (#848484). A 1px divider under the header.
- Step rows are 32px tall, 20px side padding. At the left a 16px outline icon (#858585) and the label (14px). At the right the duration (11px, #838383, one decimal), a small green check (#4cc777) and a small chevron-down (#838383). Finished rows are one uniform dim gray (#afafaf), past tense. The live row is present tense and has no duration and no check. On a live row the verb is dim and its argument (a file name, a phrase, an order number) is bright #fafafa.
- When the agent needs a decision: the status becomes a small amber hand icon and "Waiting for you" in near-white, the timer is frozen; the last row becomes an amber row "Review ..." with a one-sentence summary under it, then a bordered review block (14px radius, tab bar on top, monospace 12px lines, line numbers in #7b857c, an added line tinted #1c271e with a green plus, a removed line tinted dark red with a red minus).
- Docked assistant panel: a column docked on the right of the page, separated by a 1px #2b2b2b left border, fill #181818. Header: coral starburst, "Assistant" (14px medium) and the model name (12px, #919191), a dock-toggle icon at the right. Under it a small pill chip with a file icon and the text "This page".
- The card is a single task view: the user's prompt is the card title. There are no chat bubbles inside or around the card.
"""

CHAT_SCREENS = [
("16", "chat-docked-assistant", """
SCREEN: Servers page with a docked Assistant panel (signed in as Maya Chen, Admin). Rail item "Servers" is active. The Servers page content fills the left 58% of the content area: title "Servers" with the subtitle "3 of 4 enabled. Tools are shared with your team as <server>__<tool>." Then the cards. "WooCommerce store" expanded with a green check ring orb, "Connected", and exactly these 5 tool rows (outline icon, tool name in secondary gray, short description in tertiary gray, duration at the right): woo__list_products "List products" 1.1s; woo__create_product "Create a product" 2.4s; woo__list_orders "List orders" 1.2s; woo__get_order "Get one order" 0.8s; woo__update_order_status "Change an order's status" 1.6s. Then "Shipping quotes" collapsed with a green check ring orb and "Connected". Then "Supplier catalog" collapsed with a red ring orb with an x and "Unreachable". The primary "Add server" button is at the top right of the page. The docked Assistant panel fills the right 42% (about 560px).
Panel content, top to bottom: header row with the coral starburst, "Assistant" and "Claude Haiku 5.5", dock-toggle icon at the right. A chip row: a pill chip "This page" with a file icon, and a small plus-icon chip "Add context". Below, an empty message area with three tertiary-gray suggestion rows, each with a small arrow-up-right icon: "Which servers are unreachable?", "Show failed calls from the last hour", "Disable analytics". At the bottom a pill composer with the placeholder "Ask about this page" and a round primary send button with an arrow-up icon.
"""),
("17", "chat-run-live", """
SCREEN: Chat page, an agent run in progress. There is NO docked assistant panel on this screen: everything right of the rail is the centered chat column only, with plain empty canvas on both sides of it. Rail item "Chat" is active (signed in as Maya Chen). No conversation list. The content column is centered: a 640px wide run card vertically centered in the upper two thirds of the screen, and at the bottom a pill composer with the placeholder "Ask about your store" and a round stop button (a filled square icon on a #242424 circle) instead of a send arrow.
The run card, title "Orders today and a quote for the biggest". Model line: "Claude Haiku 5.5"; status "Working" with a spinner; timer "0:03". Rows: sparkle icon "Planned the lookup" 0.9s with check; search icon "Searched orders placed today" 1.2s with check; file icon "Read order #1043" 0.8s with check; then the live row with a truck icon: dim verb "Getting shipping quotes for" and bright argument "order #1043", no duration, no check, and a chevron-down.
"""),
("18", "chat-approval-review", """
SCREEN: Chat page, the agent has stopped for approval. There is NO docked assistant panel on this screen: everything right of the rail is the centered chat column only, with plain empty canvas on both sides of it. Rail item "Chat" is active (signed in as Maya Chen). The content column is centered with a 640px wide run card; at the bottom a dimmed pill composer with the placeholder "Waiting for your decision".
The run card, title "Mark order #1043 as completed". Model line "Claude Haiku 5.5"; status: amber hand icon and "Waiting for you"; timer "0:06". Rows with checks: sparkle icon "Planned the change" 0.7s; file icon "Read order #1043" 0.8s; search icon "Searched shipping and stock for order #1043" 1.0s. Then the amber row, amber icon and "Review change to 1 order", and under it the summary in gray: "Set order #1043 from Processing to Completed. WooCommerce emails the customer."
Then the bordered review block with a tab bar showing one active tab "Order #1043" with "+2" in green and "-1" in red. Lines: line 1 removed, red tint, "- status: processing"; line 2 added, green tint, "+ status: completed"; line 3 added, green tint, "+ date_completed: 2026-10-24 05:02". Under the review block, left aligned with the summary text: a primary pill "Approve" with a check icon and a secondary pill "Deny" with an x icon, and at the right the ghost link "Open run". Approve is the only filled button on the screen.
"""),
("19", "chat-run-done", """
SCREEN: Chat page, the run has finished and folded. There is NO docked assistant panel on this screen: everything right of the rail is the centered chat column only, with plain empty canvas on both sides of it. Rail item "Chat" is active (signed in as Maya Chen). Centered 640px column. At the bottom a pill composer with the placeholder "Ask about your store" and a round primary send button.
The finished run card: title "Mark order #1043 as completed". Model line "Claude Haiku 5.5"; status: a green check and "Done in 6s", no timer. In place of the step rows, one folded line with a small layers icon: "4 steps . 3.4s" and a chevron-down at the right. Under a divider, the result block: a row with a green check icon and "Changed 1 order"; below it a compact row in a #1f1f1f inset with 14px radius: "Order #1043", "Processing" in tertiary gray, a small arrow, "Completed" in primary text. Action bar: a secondary pill "Undo" with an undo icon, a secondary pill "Open order" with an arrow-up-right icon, and at the right the ghost link "Open run".
Under the card, the assistant's reply as plain text with no bubble: "Order #1043 is now Completed. WooCommerce emailed the customer."
"""),
("20", "chat-applied-tint-undo", """
SCREEN: Servers page with the docked Assistant panel, just after the agent applied a change. Rail item "Servers" is active. Left 58%: the Servers page with cards "WooCommerce store" (collapsed), "Shipping quotes" (collapsed), "Supplier catalog" (collapsed, red ring, Unreachable) and "Analytics". The "Analytics" card has just been enabled: its orb is now a green check ring, its status reads "Connected", and a soft horizontal tint sweep (a faint blue-white band, about 12% opacity) is crossing the card from left to right, with the left half of the card already tinted slightly and the right half not yet.
Right 42%: the docked Assistant panel with a "This page" chip. A finished run card, title "Enable analytics", status green check "Done in 3s", a folded line "3 steps . 2.1s" with a chevron, a result row with a green check "Enabled 1 server", an inset row "Analytics" with "Disabled" in tertiary gray, an arrow, and "Connected" in primary text. Action bar: a secondary pill "Undo" with an undo icon, and the ghost link "Open run". Below it the plain reply: "Analytics is enabled. Its 4 tools are now available to your team." Composer at the bottom with the placeholder "Ask about this page".
"""),
("21", "chat-motion-step-enter", """
SCREEN: A motion storyboard on a plain #121212 canvas. NO left rail, NO sidebar, NO wordmark, NO app shell. The board fills the whole frame. Title top left "New step . row enters, card grows" (17px medium), under it in tertiary "The card is centered, so its top edge rises as the bottom drops. Real time, no speed-up."
A grid of 5 large equal panels on a #0c0c0c band: three in the top row, two in the bottom row, and the empty third cell of the bottom row holds the callouts as a short numbered list. Each panel is about 440px wide and 340px tall, shows the same run card at the same scale (the card is vertically centered in the panel), and has a time label under it: "0 ms", "33 ms", "83 ms", "170 ms", "500 ms". A thin dashed outline in each panel marks where the card's top and bottom edges were at 0 ms, so the growth is visible.
Card content in every panel: title "Add rate limiting to the public API", model line "Claude Haiku 5.5" with "Working" and "0:03", rows: "Planned the change" 1.2s check; "Read src/api/routes.ts" (live at 0 ms).
Panel 1 (0 ms): rows "Planned the change" 1.2s with check, and the live row "Reading src/api/routes.ts" with dim verb and bright argument, no duration.
Panel 2 (33 ms): the live row has just finished: its text is now "Read src/api/routes.ts" in uniform gray, the duration "0.9s" is appearing, and a green check is half drawn. A new row "Searching for rate limit middleware" is peeking up from behind the card's bottom edge, only its top half visible and clipped. The card has grown a little.
Panel 3 (83 ms): the check is fully drawn, the chevron is visible. The new row is about 70% visible. The card top has risen a few pixels and the bottom has dropped.
Panel 4 (170 ms): the new row is fully visible with dim verb "Searching for" and bright argument "rate limit middleware". Card nearly at its final height.
Panel 5 (500 ms): settled. Three rows. Card is exactly one row (32px) taller than in panel 1.
Callouts as a tertiary-gray list: "1 Previous row finishes and next row starts in the same frame", "2 Check draws in about 70 ms", "3 New row peeks from behind the bottom edge", "4 Card grows on one spring, about 0.5 s to settle", "5 Top edge rises half a row, bottom drops half a row".
"""),
("22", "chat-motion-approval", """
SCREEN: A motion storyboard on a plain #121212 canvas. NO left rail, NO sidebar, NO wordmark, NO app shell. The board fills the whole frame. Title top left "Approval . live row becomes a review" (17px medium), under it in tertiary "Real time. The timer stops. The review opens in two beats."
A grid of 6 panels on a #0c0c0c band, two rows of three, each about 440px wide and 400px tall, each showing the same crop of the run card (the header row at the top and the last rows below it), with a time label under each: "0 ms", "67 ms", "133 ms", "270 ms", "500 ms", "800 ms".
Card content: title "Add rate limiting to the public API"; model line "Claude Haiku 5.5"; rows with checks "Searched for the API key on requests" 0.9s; then the last row.
Panel 1 (0 ms): status "Working" with a spinner, timer "0:06"; last row live: dim "Preparing" and bright "changes", no duration.
Panel 2 (67 ms): the spinner has been replaced by a small amber hand icon; the status label is being revealed from the left and is clipped, reading "Waiting fo". The last row is cross-fading into an amber row.
Panel 3 (133 ms): status "Waiting for you" fully visible in near-white; timer "0:06" frozen. The last row is now amber: "Review changes to 2 files". Under it the summary sentence is fading in: "Add a Redis rate limiter, 60 requests a minute per API key."
Panel 4 (270 ms): the review block has appeared under the summary with its tab bar "rate-limit.ts +15" and "routes.ts +2 -1", and two code lines visible with green tint.
Panel 5 (500 ms): the review block is taller, about 8 of 15 lines visible, revealed top to bottom at a steady speed; the card's top edge stays fixed.
Panel 6 (800 ms): all lines visible; below the block a primary pill "Approve" with a check icon, a secondary pill "Deny" with an x icon and the ghost link "Open run".
Add a short numbered list of callouts in tertiary gray under the grid: "1 Spinner swaps to a hand, label reveals by width", "2 Live row changes in place, no completed row", "3 Review opens in two beats: shell, then lines", "4 Lines reveal at a steady pace, not a spring".
"""),
]
SCREENS += CHAT_SCREENS


def key_from(args):
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    if args.env_file:
        for line in open(args.env_file):
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("No OPENAI_API_KEY found. Set it or pass --env-file.")


def build_prompt(body, num="00"):
    extra = CHAT_STYLE if num >= "16" else ""
    return (STYLE + extra + DATA + body).strip()


def render(client, num, name, body, quality, force):
    path = OUT / f"{num}-{name}.png"
    if path.exists() and not force:
        return f"{num}-{name}: exists, skipped"
    prompt = build_prompt(body, num)
    last = None
    for attempt in range(3):
        try:
            t = time.time()
            files = [open(p, "rb") for p in (REFS_CHAT if num >= "16" else REFS)]
            r = client.images.edit(model=MODEL, image=files, prompt=prompt, size=SIZE, quality=quality)
            for f in files:
                f.close()
            path.write_bytes(base64.b64decode(r.data[0].b64_json))
            u = getattr(r, "usage", None)
            tok = getattr(u, "output_tokens", "?") if u else "?"
            return f"{num}-{name}: ok {time.time() - t:.0f}s out_tokens={tok}"
        except Exception as e:  # retry on rate limits and transient errors
            last = str(e)[:200]
            time.sleep(4 * (attempt + 1))
    return f"{num}-{name}: FAILED {last}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--quality", default="high")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dump-prompts", action="store_true")
    args = ap.parse_args()

    if args.list:
        for n, name, _ in SCREENS:
            print(n, name)
        return
    OUT.mkdir(parents=True, exist_ok=True)
    if args.dump_prompts:
        md = [f"# Mockup prompts\n\nModel `{MODEL}`, size {SIZE}, three reference frames attached to every call "
              f"({', '.join(p.name for p in REFS)}; screens 16 and up use {', '.join(p.name for p in REFS_CHAT)}).\n\n## Shared style block\n\n```text\n{STYLE.strip()}\n```\n\n## Chat agent style block (screens 16 and up)\n\n```text\n{CHAT_STYLE.strip()}\n```\n\n## Shared data\n\n```text\n{DATA.strip()}\n```\n"]
        for n, name, body in SCREENS:
            md.append(f"\n## {n} {name}\n\n```text\n{body.strip()}\n```\n")
        (OUT / "prompts.md").write_text("".join(md))
        print("wrote", OUT / "prompts.md")
        return

    from openai import OpenAI
    client = OpenAI(api_key=key_from(args))
    todo = [s for s in SCREENS if not args.only or s[0] in args.only or s[1] in args.only]
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(render, client, n, name, body, args.quality, args.force) for n, name, body in todo]
        for f in cf.as_completed(futs):
            print(f.result(), flush=True)


if __name__ == "__main__":
    main()
