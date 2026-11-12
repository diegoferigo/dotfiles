## Question

Stream a file line by line or in chunks?

## Context

- The new grammar works on a whole string today.
- Line by line is simple, but a token that spans two lines breaks it.
- Chunks need a carry-over buffer between reads.

## Options

1. **Line by line.** Cost: low. Risk: wrong tokens when a token spans lines. Reversible: yes.
2. **Fixed chunks with a carry-over buffer.** Cost: medium. Risk: boundary bugs. Reversible: yes.

## Recommendation

Option 2, with a property test over random split points.
