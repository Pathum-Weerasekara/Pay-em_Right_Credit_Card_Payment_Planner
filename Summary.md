# Summary of Changes

## 1. Manually Managed Credit Cards
- **Dashboard Modal:** Added an "Add Manual Card" button and modal overlay (`#add-manual-card-modal`) in `dashboard.html` to create cards not supported by Plaid.
- **Backend Endpoints:** Added `/dashboard/manual-card/add/{year}/{month}` to handle manual card creation, generating a unique ID (`manual_cc_<id>`), and setting up initial plan and monthly summary data.
- **Default Balances:** Initialized both Spent This Month (`cc_spending`) and Planned Payment (`planned_amount`) to default to the positive current balance entered in the modal.
- **Card Details Form:** Updated `card_detail.html` to display manual input fields (Current Card Balance, Spent, Payments Made, and Refunds/Credits) when the card source is manual, while hiding Plaid transaction tables.
- **Card Deletion:** Added a "Delete Manual Card" button on the detail page that safely removes the manual card and all its records.

## 2. Table Row Numbering
- **Sequential Numbers:** Added a `#` column to the table. Numbers are dynamically recalculated and rendered (e.g., `1.`, `2.`, `3.`) via JavaScript (`refreshFilteredCards()`) whenever rows are shown, hidden, or sorted.

## 3. Alphabetical Column Sorting
- **Interactive Toggles:** Added `↕️` sort buttons next to the **Card Name** and **Pay From** table headers.
- **Client-Side Sorting:** Implemented `toggleSort(columnType)` in JS to order the rows alphabetically on the fly without page reloads.
- **Card Links:** Turned the card names into clickable links to open details page.

## 4. Restored Dashboard Layout Components
- **Comments Column:** Re-added the Comments inputs to the table.
- **Paid Checkbox Column:** Re-added the Paid checkboxes along with the `onPaidChange(checkbox)` JS handler to update the budgets live.
- **Pay From Dropdown Options:** Restored split options (`Pathum Manual`, `Pathum Auto`, `Ramesha Manual`, `Ramesha Auto`).
- **Payer Select Colors:** Restored blue background styling for Pathum and pink background styling for Ramesha on load and select change.

## 5. UI Layout Adjustments
- **Dropdown Padding:** Increased right-side padding inline in the head of `dashboard.html` to `1.85rem` on `.plan-payer-select` select boxes to prevent the browser dropdown indicator arrow from overlapping the option labels.
- **Column Min-Width:** Restored the `min-width: 165px;` style on the **Pay From** table header `<th>` cell in `dashboard.html` to ensure the select elements have enough horizontal width to display option names fully (preventing truncation to "Ramesha Manu").
