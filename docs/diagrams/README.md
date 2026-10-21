# Diagrams

Every diagram is written in [Mermaid](https://mermaid.js.org) (`*.mmd`, the source of truth) and exported
to SVG (used in the report) and PNG. GitHub renders Mermaid directly if you paste the source into a
` ```mermaid ` block.

| File | Shows |
|---|---|
| `component` | services, databases, the mock bank and every call between them (direction + protocol) |
| `sequence-happy-path` | the main transaction, booking a ticket, when everything works |
| `sequence-failure-step3` | the same transaction when step 3 fails, with the compensations |
| `state-booking` | booking state machine (BR5) |
| `state-hold-payment` | seat-hold and payment state machines |
| `er-booking`, `er-inventory`, `er-payment`, `er-notification` | one ER diagram per service database |
| `idempotency-flow` | how an Idempotency-Key is claimed, replayed or rejected |
| `concurrency-race` | why check-then-update oversells and the conditional UPDATE does not |

To regenerate after editing (needs Node.js; uses a local Chrome/Chromium):

```bash
npx -p @mermaid-js/mermaid-cli mmdc -i component.mmd -o component.svg -b white
npx -p @mermaid-js/mermaid-cli mmdc -i component.mmd -o component.png -b white -s 2
```
