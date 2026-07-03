# Report Prompt Contract

LLM usage is optional and is not implemented in v0.1. When added, the LLM must only convert computed JSON into natural language. It must not calculate scores.

Required behavior:

- Do not say "buy" or "sell" as an instruction.
- Explain score meaning clearly.
- Mention structure, volume, liquidity, momentum, and risk.
- Warn clearly when FOMO Index is high.
- Leave final judgment to the user.

